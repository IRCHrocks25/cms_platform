"""CMS-65: custom-domain verification asks the domain's authoritative
nameservers directly instead of the container's caching resolver.

Only the network edge is faked: ``_nameserver_ips`` (which NS to ask) and
``_query_nameserver`` (one UDP/TCP exchange). The responses are real dnspython
messages, so answer parsing and CNAME handling run for real.
"""
from unittest.mock import patch

import dns.exception
import dns.message
import dns.rcode
import dns.rrset
from django.test import TestCase, override_settings

from core.models import CustomDomain, Template, Tenant
from core.services import custom_domains

TARGET_IP = "203.0.113.7"
OLD_IP = "198.51.100.9"
NS_A = "192.0.2.1"
NS_B = "192.0.2.2"


def _response(qname, *rrsets, rcode=dns.rcode.NOERROR):
    query = dns.message.make_query(qname, "A")
    response = dns.message.make_response(query)
    response.set_rcode(rcode)
    for name, rdtype, value in rrsets:
        response.answer.append(dns.rrset.from_text(name, 900, "IN", rdtype, value))
    return response


def _answers(table):
    """Fake one exchange: ``table[(ns_ip, qname)]`` is a response or an exception."""

    def query(ns_ip, qname):
        result = table[(ns_ip, qname)]
        if isinstance(result, Exception):
            raise result
        return result

    return query


class AuthoritativeLookupTests(TestCase):
    def _resolve(self, domain, nameservers, table):
        with patch.object(
            custom_domains, "_nameserver_ips", return_value=nameservers
        ), patch.object(custom_domains, "_query_nameserver", side_effect=_answers(table)):
            return custom_domains.resolve_a_records(domain)

    def test_returns_the_authoritative_answer(self):
        table = {
            (NS_A, "acme.com"): _response("acme.com", ("acme.com.", "A", TARGET_IP)),
            (NS_B, "acme.com"): _response("acme.com", ("acme.com.", "A", TARGET_IP)),
        }
        self.assertEqual(self._resolve("acme.com", [NS_A, NS_B], table), [TARGET_IP])

    def test_disagreeing_nameservers_return_every_address_seen(self):
        """Mid-propagation, one NS still serves the old record. Let's Encrypt
        may ask either, so both addresses must surface."""
        table = {
            (NS_A, "acme.com"): _response("acme.com", ("acme.com.", "A", TARGET_IP)),
            (NS_B, "acme.com"): _response("acme.com", ("acme.com.", "A", OLD_IP)),
        }
        self.assertEqual(
            self._resolve("acme.com", [NS_A, NS_B], table), sorted([TARGET_IP, OLD_IP])
        )

    def test_unreachable_nameserver_is_skipped_when_another_answers(self):
        table = {
            (NS_A, "acme.com"): dns.exception.Timeout(),
            (NS_B, "acme.com"): _response("acme.com", ("acme.com.", "A", TARGET_IP)),
        }
        self.assertEqual(self._resolve("acme.com", [NS_A, NS_B], table), [TARGET_IP])

    def test_no_nameserver_answers_returns_empty(self):
        table = {(NS_A, "acme.com"): dns.exception.Timeout()}
        self.assertEqual(self._resolve("acme.com", [NS_A], table), [])

    def test_nxdomain_returns_empty(self):
        table = {(NS_A, "acme.com"): _response("acme.com", rcode=dns.rcode.NXDOMAIN)}
        self.assertEqual(self._resolve("acme.com", [NS_A], table), [])

    def test_no_nameservers_found_returns_empty(self):
        self.assertEqual(self._resolve("acme.com", [], {}), [])

    def test_follows_cname_inside_the_same_answer(self):
        table = {
            (NS_A, "www.acme.com"): _response(
                "www.acme.com",
                ("www.acme.com.", "CNAME", "acme.com."),
                ("acme.com.", "A", TARGET_IP),
            ),
        }
        self.assertEqual(self._resolve("www.acme.com", [NS_A], table), [TARGET_IP])

    def test_follows_cname_into_another_zone(self):
        """An out-of-zone CNAME target isn't in the answer; resolve it
        authoritatively in its own zone."""
        nameservers = {"www.acme.com": [NS_A], "edge.other.net": [NS_B]}
        table = {
            (NS_A, "www.acme.com"): _response(
                "www.acme.com", ("www.acme.com.", "CNAME", "edge.other.net.")
            ),
            (NS_B, "edge.other.net"): _response(
                "edge.other.net", ("edge.other.net.", "A", TARGET_IP)
            ),
        }
        with patch.object(
            custom_domains, "_nameserver_ips", side_effect=lambda d: nameservers[d]
        ), patch.object(custom_domains, "_query_nameserver", side_effect=_answers(table)):
            self.assertEqual(custom_domains.resolve_a_records("www.acme.com"), [TARGET_IP])

    def test_cname_loop_terminates(self):
        table = {
            (NS_A, "a.acme.com"): _response(
                "a.acme.com", ("a.acme.com.", "CNAME", "b.acme.com.")
            ),
            (NS_A, "b.acme.com"): _response(
                "b.acme.com", ("b.acme.com.", "CNAME", "a.acme.com.")
            ),
        }
        self.assertEqual(self._resolve("a.acme.com", [NS_A], table), [])

    def test_zone_lookup_failure_returns_empty(self):
        with patch.object(
            custom_domains, "_nameserver_ips", side_effect=dns.exception.DNSException()
        ):
            self.assertEqual(custom_domains.resolve_a_records("acme.com"), [])


@override_settings(CUSTOM_DOMAIN_TARGET_IP=TARGET_IP)
class VerifyRuleTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.create_user("o", password="x")
        tpl = Template.objects.create(
            name="T", html_source="<section data-section='x'></section>"
        )
        tenant = Tenant.objects.create(
            name="A", subdomain="a", template=tpl, owner=owner
        )
        self.cd = CustomDomain.objects.create(tenant=tenant, domain="acme.com")

    def _verify(self, resolved):
        with patch.object(custom_domains, "resolve_a_records", return_value=resolved):
            return custom_domains.verify_custom_domain(self.cd)

    def test_exact_match_verifies_and_stamps_verified_at(self):
        self.assertEqual(self._verify([TARGET_IP]), (True, [TARGET_IP]))
        self.cd.refresh_from_db()
        self.assertTrue(self.cd.is_verified)
        self.assertIsNotNone(self.cd.verified_at)

    def test_target_plus_a_stale_address_does_not_verify(self):
        """LE may validate against the stale address, so a mixed answer is
        the www.compassdesignbuild.com failure, not a pass."""
        verified, resolved = self._verify([OLD_IP, TARGET_IP])
        self.assertFalse(verified)
        self.assertEqual(resolved, [OLD_IP, TARGET_IP])
        self.cd.refresh_from_db()
        self.assertFalse(self.cd.is_verified)

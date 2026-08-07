from ninja import NinjaAPI


api = NinjaAPI(title="Katek Sites API", version="1.0.0")


@api.get("/health")
def health(request):
    return {"status": "ok"}

from fastapi import APIRouter

router = APIRouter()

@router.get("/api")
def index():
    return {"message": "Athena Service Assistant"}

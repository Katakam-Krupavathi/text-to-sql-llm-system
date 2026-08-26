from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.upload_routes import router as upload_router
from app.routers.query_read import router as query_router

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(upload_router)
app.include_router(query_router)

@app.get("/")
def home():

    return {
        "message":"Text-to-SQL API Running"
    }
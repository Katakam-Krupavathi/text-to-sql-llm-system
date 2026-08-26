from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str
    mode: str
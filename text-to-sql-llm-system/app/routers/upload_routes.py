from fastapi import APIRouter, UploadFile, File
import shutil
import os

from app.services.file_parser import parse_file
from app.services.db_converter import create_temp_db
from app.core.schema_extractor import extract_schema
import app.utils.common as common
from app.core.context_store import schema_cache

router = APIRouter()

UPLOAD_FOLDER = "app/uploads"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@router.post("/upload")  
async def upload_file(file: UploadFile = File(...)):            # dini main role user ichina type file ani kanipettidhi

    file_path = f"{UPLOAD_FOLDER}/{file.filename}"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    result = parse_file(file_path)

    # Direct database upload
    if isinstance(result, str) and result == "DATABASE_FILE":   # file tiskunidhi

        common.current_db = file_path

        schema = extract_schema(file_path)
        schema_cache["current_schema"] = schema

        return {
            "message": "Database uploaded successfully",
            "database_path": file_path,
            "schema": schema
        }

    # CSV / Excel / JSON
    db_path = create_temp_db(result)

    common.current_db = db_path

    schema = extract_schema(db_path)   # ee method call chesi schema techukunidhi

    return {
        "message": "File converted successfully",
        "database_created": db_path,
        "schema": schema
    }
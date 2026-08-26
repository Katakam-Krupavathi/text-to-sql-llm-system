import pandas as pd
import json


# ippudu manam allow chese files like csv , excel ala user upload cheyadaniki use chestham
def parse_file(file_path: str):

    file_path = file_path.lower()

    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)

    elif file_path.endswith(".xlsx") or file_path.endswith(".xls"):
        df = pd.read_excel(file_path)

    elif file_path.endswith(".json"):

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]

        df = pd.DataFrame(data)

    elif file_path.endswith(".db"):
        return "DATABASE_FILE"

    else:
        raise ValueError(
            f"Unsupported file type: {file_path}"
        )

    return df
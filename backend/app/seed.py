from app.database import SessionLocal
from app.seed_data import seed_database


if __name__ == "__main__":
    with SessionLocal() as session:
        seed_database(session)

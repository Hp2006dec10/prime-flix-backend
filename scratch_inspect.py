import sys
import os
from app.db.session import SessionLocal
from app.db.models import Movie, Director

db = SessionLocal()
movies = db.query(Movie).all()

with open("scratch_movies.txt", "w", encoding="utf-8") as f:
    for m in movies:
        d_name = m.director.name if m.director else "NO DIRECTOR"
        f.write(f"ID: {m.id} | TMDB: {m.tmdb_id} | Title: {m.title} | DirectorID: {m.director_id} | Director: {d_name}\n")

print("Done writing scratch_movies.txt")

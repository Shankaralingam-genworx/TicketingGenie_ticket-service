"""CORS middleware.

FIX: allow_origins=["*"] with allow_credentials=True is rejected by browsers
     (CORS spec prohibits wildcard + credentials). Use explicit origins instead.
     In development, specify your frontend's origin. For production, read from env.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def add_cors_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",   # React dev server
            "http://localhost:5173",   # Vite dev server
            "http://localhost:8080",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

import uvicorn


def main():
    """
    Launch the server. API keys and other secrets must already be set in the
    environment (DEBRID_API_KEY, FICHIER_API_KEY, ENCRYPTION_KEY_B64, and the
    rest of the values read by config.py).
    """
    print("\nIniciando servidor Uvicorn...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        lifespan="on"
    )


if __name__ == "__main__":
    main()

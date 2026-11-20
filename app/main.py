from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def hello():
    return {"message": "Hello from Paper DDL Reminder"}


@app.get("/ping")
def ping():
    return {"status": "ok"}
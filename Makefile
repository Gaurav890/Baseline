.PHONY: dev api web test evals build

dev:
	docker compose up --build

api:
	cd backend && uvicorn app.main:app --reload --port 8000

web:
	cd frontend && npm run dev

test:
	cd backend && pytest -q

evals:
	cd backend && python -m app.evals.cli --provider mock --all

build:
	cd frontend && npm run build


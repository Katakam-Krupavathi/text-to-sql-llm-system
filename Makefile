.PHONY: demo down eval test build clean

demo:
	@echo "=========================================================="
	@echo "Starting Text-to-SQL Agent Stack (Postgres + API + UI)..."
	@echo "=========================================================="
	docker compose up --build -d
	@echo ""
	@echo "✓ Services are starting up!"
	@echo "  - Streamlit UI:       http://localhost:8501"
	@echo "  - FastAPI Swagger UI: http://localhost:8000/docs"
	@echo "  - Health Check:       http://localhost:8000/health"
	@echo "=========================================================="

down:
	@echo "Stopping and removing all containers and volumes..."
	docker compose down -v

eval:
	@echo "Running Execution Accuracy (EX) evaluation inside Docker stack..."
	docker compose exec backend python scripts/run_eval.py --eval-set tests/eval/eval_set.json --threshold 0.70

test:
	@echo "Running test suite inside Docker stack..."
	docker compose exec backend pytest -v -o asyncio_mode=auto

clean:
	@echo "Cleaning local cache directories and temporary files..."
	rm -rf vector_cache/ .pytest_cache/ htmlcov/ __pycache__ */__pycache__

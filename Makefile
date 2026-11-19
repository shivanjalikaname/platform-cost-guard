.PHONY: install lint test demo tf-check all

install:
	pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

test:
	pytest -v

demo:
	python examples/demo.py

tf-check:
	cd terraform/github-oidc-role && terraform fmt -check && terraform init -backend=false -input=false && terraform validate

all: lint test demo

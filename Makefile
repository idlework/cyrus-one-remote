.DEFAULT_GOAL := help
.PHONY: help install dev test lint types check clean

help:  ## list the targets
	@grep -hE '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) \
		| sed -e 's/:.*##/·/' | column -t -s'·'

install:  ## create .venv with what cyrus.py needs to run
	python3 -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip bleak

dev: install  ## install the linter and type checker too
	.venv/bin/pip install --quiet ruff pyright

test:  ## run the protocol tests (no hardware, no network)
	.venv/bin/python test_protocol.py

lint:  ## style and common mistakes
	.venv/bin/ruff check cyrus.py test_protocol.py

types:  ## type check
	.venv/bin/pyright

check: lint types test  ## everything above, in one go

clean:  ## remove the venv and caches
	rm -rf .venv __pycache__ .ruff_cache

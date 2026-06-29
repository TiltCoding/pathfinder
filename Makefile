# ai-pathfinder dev shortcuts. Thin wrappers over dev.py (the single source of
# truth for the local dev loop) so the two entry points never drift. Override the
# interpreter with `make PY=python test` if `python3` isn't your launcher.
PY ?= python3

.PHONY: test serve lint preview queue check

test:
	$(PY) dev.py test

serve:
	$(PY) dev.py serve

lint:
	$(PY) dev.py lint

preview:
	$(PY) dev.py preview

queue:
	$(PY) dev.py queue

check:
	$(PY) dev.py check

# ai-pathfinder dev shortcuts (python3 only, no extra deps).
# Run tests or the companion server without remembering the full commands.

.PHONY: test serve

test:
	python3 -m unittest discover -s tests

serve:
	python3 scripts/server.py --root "$$(pwd)"

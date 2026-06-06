.PHONY: test check

test:
	python -m unittest discover -v

check:
	python -m py_compile run_daily_signal.py main.py process_feedback.py send_training_digest.py api/feedback.py app/*.py scripts/*.py test*.py
	python -m unittest discover -v

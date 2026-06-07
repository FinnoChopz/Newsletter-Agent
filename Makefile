.PHONY: test check

test:
	python -m unittest discover -v

check:
	python -m py_compile run_daily_signal.py run_scheduled_profiles.py main.py process_feedback.py send_training_digest.py web_console.py api/feedback.py app/*.py scripts/*.py test*.py
	python -m unittest discover -v

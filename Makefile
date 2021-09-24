.PHONY: dependencies unit up_dev up_prod

dependencies:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install -r test-requirements.txt

unit: dependencies
	python3 -m unittest discover -s tests/ -p 'test_*'


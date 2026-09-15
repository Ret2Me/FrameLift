CARGO ?= cargo
ROOT := ../..

.PHONY: all evidence verify
all: evidence
	pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex
	bibtex main
	pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex
	pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex

evidence:
	cd $(ROOT) && $(CARGO) run --locked --example paper_evidence -- render publication/decoder-paper-v2/evidence publication/decoder-paper-v2/generated

verify:
	cd $(ROOT) && $(CARGO) run --locked --example paper_evidence -- verify publication/decoder-paper-v2/evidence
	cd $(ROOT) && $(CARGO) test --locked --example paper_evidence

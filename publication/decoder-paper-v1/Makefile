TEMPLATE := $(CURDIR)/sources/ieee-template/ACCESS_latex_template_20260513
export TEXINPUTS := .:$(TEMPLATE)//:
export TFMFONTS := .:$(TEMPLATE)//:
export T1FONTS := .:$(TEMPLATE)//:
export TEXFONTMAPS := .:$(TEMPLATE)//:

.PHONY: all verify
all:
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
	bibtex main
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
	cp main.pdf telemetry-yield-preliminary-paper.pdf

verify:
	cd evidence && sha256sum -c checksums.sha256
	node verify-pdf-log.cjs

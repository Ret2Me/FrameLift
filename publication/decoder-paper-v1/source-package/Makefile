TEMPLATE := $(CURDIR)/sources/ieee-template/ACCESS_latex_template_20260513
export TEXINPUTS := .:$(TEMPLATE)//:
export TFMFONTS := .:$(TEMPLATE)//:
export T1FONTS := .:$(TEMPLATE)//:
export TEXFONTMAPS := .:$(TEMPLATE)//:
.PHONY: all verify
all: sources/ieee-template/ACCESS_latex_template_20260513/ieeeaccess.cls
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
	bibtex main
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
	pdflatex -interaction=nonstopmode -halt-on-error main.tex
sources/ieee-template/ACCESS_latex_template_20260513/ieeeaccess.cls:
	mkdir -p sources/ieee-template
	unzip -q ieee-access-template.zip -d sources/ieee-template
verify:
	node verify-pdf-log.cjs

.PHONY: build test install uninstall

build:
	cargo build --locked --release --workspace --manifest-path native/Cargo.toml

test: build
	python3 scripts/validate.py --alis-root "$(ALIS_ROOT)" $(if $(strip $(ALIS_INTERGRATION_ROOT)),--alis-intergration-root "$(ALIS_INTERGRATION_ROOT)")

install: build
	python3 scripts/install.py install

uninstall:
	python3 scripts/install.py uninstall

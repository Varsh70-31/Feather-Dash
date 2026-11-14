# Feather Dash -- friendly entry points.
#
# `make` on its own prints the list below; every target documented there is a
# line with a `## comment`, so the help can never drift from reality.
#
# Written for GNU Make 3.81 -- the version Apple still ships -- so no .ONESHELL,
# no $(file ...), no := tricks that need 4.x.

SHELL := /bin/bash

# The project's own virtualenv if it exists, otherwise whatever python3 is on
# the PATH. The venv is what has pygame; the system python3 on macOS does not,
# and running the game with it produces a confusing ImportError.
VENV_PY := $(CURDIR)/.venv/bin/python
# The final `echo python3` matters: on a machine with neither a venv nor a
# python3 on PATH, an empty PY turns every recipe into a bare `main.py`, whose
# error message explains nothing.
PY := $(shell test -x $(VENV_PY) && echo $(VENV_PY) || command -v python3 || echo python3)

ANDROID := $(CURDIR)/packaging/android/build.sh

# pygame opens a window and a sound device unless told otherwise. Every target
# that imports pygame without wanting either prepends this.
HEADLESS := SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy

# Lint scope.
LINT_EXCLUDE := .venv,bin,.buildozer,.git

.DEFAULT_GOAL := help
.PHONY: help run assets art audio test selftest lint format check \
        android-debug android-release android-install android-logcat \
        android-shell android-validate android-clean keystore \
        dev clean distclean version

# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------
help: ## Show this help
	@printf '\nFeather Dash\n\n'
	@printf '  usage: make <target>\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@printf '\n  python: $(PY)\n\n'

# --------------------------------------------------------------------------
# day to day
# --------------------------------------------------------------------------
run: ## Run the game
	$(PY) main.py

dev: ## Install the development dependencies into .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

version: ## Print the version the Android build will use
	@$(PY) -c "import re,io;print(re.search(r\"__version__ = ['\\\"](.*)['\\\"]\", io.open('featherdash/__init__.py').read()).group(1))"

# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------
assets: art audio ## Regenerate every generated asset (art + audio)

# ORDER MATTERS. gen_brand composites the wordmark out of logo.png, which
# gen_ui writes, so branding has to run last. The generators are also still
# being written, so each one is skipped with a note rather than failing the
# whole run if it is not there yet.
ART_GENERATORS := gen_world.py gen_props.py gen_bird.py gen_ui.py gen_brand.py

art: ## Regenerate the world, sprite, UI and branding art
	@for g in $(ART_GENERATORS); do \
		if [ -f "tools/$$g" ]; then \
			echo "==> $$g"; \
			$(PY) "tools/$$g" || exit $$?; \
		else \
			echo "==> $$g not present, skipping"; \
		fi; \
	done

audio: ## Regenerate the sound effects and music
	@if [ -f tools/gen_audio.py ]; then \
		echo "==> gen_audio.py"; \
		$(PY) tools/gen_audio.py; \
	else \
		echo "==> gen_audio.py not present, skipping"; \
	fi

# --------------------------------------------------------------------------
# quality
# --------------------------------------------------------------------------
test: ## Run the test suite, headless
	@# pytest exits 5 when it collects nothing, which is not a failure while
	@# the suite is still being written.
	@$(HEADLESS) $(PY) -m pytest tests -q; \
	rc=$$?; \
	if [ $$rc -eq 5 ]; then echo "(no tests collected)"; exit 0; fi; \
	exit $$rc

selftest: ## Run the packaging self-test
	$(HEADLESS) $(PY) packaging/android/selftest.py

lint: ## Lint the python and shell sources
	@echo "==> ruff (syntax and undefined names -- blocking)"
	$(PY) -m ruff check --select E9,F63,F7,F82 --exclude $(LINT_EXCLUDE) .
	@echo "==> ruff (project rules from pyproject.toml -- blocking)"
	$(PY) -m ruff check .
	@echo "==> shell"
	@for f in packaging/android/*.sh; do echo "   $$f"; bash -n "$$f" || exit 1; done
	@# NOT `command -v shellcheck && shellcheck ... || echo ...`. That idiom
	@# cannot tell "shellcheck is absent" from "shellcheck found something":
	@# a real violation fires the || arm, prints "not installed" -- which is a
	@# lie -- and exits 0, so `make lint` and `make check` stay green over a
	@# finding. An explicit if/else keeps the two outcomes distinct and lets
	@# shellcheck's own exit status fail the target.
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck -S warning packaging/android/*.sh; \
	else \
		echo "   (shellcheck not installed: brew install shellcheck)"; \
	fi

check: lint test selftest ## Everything CI runs, locally
	$(PY) packaging/android/validate_spec.py buildozer.spec

# --------------------------------------------------------------------------
# android
# --------------------------------------------------------------------------
# All of these go through packaging/android/build.sh, which runs buildozer in
# a Linux container -- buildozer does not work on macOS.

android-debug: ## Build a debug APK (Docker; minutes warm, ~45 min cold)
	$(ANDROID) debug

android-release: ## Build a signed .aab for Play (needs the keystore env vars)
	$(ANDROID) release

android-install: ## adb install the latest debug APK onto a connected device
	$(ANDROID) install

android-logcat: ## Tail the game's log from a connected device
	$(ANDROID) logcat

android-shell: ## Open a shell inside the Android build container
	$(ANDROID) shell

android-validate: ## Check buildozer.spec against the real buildozer's key list
	$(ANDROID) validate

android-clean: ## Delete the Android build output and per-project cache
	$(ANDROID) clean

keystore: ## Create the Play upload keystore (run once, ever)
	packaging/android/make_keystore.sh

# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------
clean: ## Remove build output and python caches
	rm -rf bin .buildozer .pytest_cache .ruff_cache
	find . -path ./.venv -prune -o -name '__pycache__' -type d -print0 2>/dev/null \
		| xargs -0 rm -rf 2>/dev/null || true
	find . -path ./.venv -prune -o -name '*.py[co]' -type f -print0 2>/dev/null \
		| xargs -0 rm -f 2>/dev/null || true

distclean: clean ## ...and the Android SDK/NDK caches (next build: ~45 minutes)
	$(ANDROID) clean --all

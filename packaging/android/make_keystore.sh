#!/usr/bin/env bash
#
# Feather Dash -- generate the Google Play UPLOAD keystore.
#
# You run this exactly once, ever. The file it produces is the only thing that
# proves to Google that an update to Feather Dash came from you.
#
# Written for bash 3.2 (the version macOS ships).
#
set -euo pipefail

# Default location is OUTSIDE the repository on purpose. A keystore inside a
# git working tree is one `git add -A` away from being published forever.
DEFAULT_KEYSTORE="${HOME}/.featherdash/upload.keystore"
DEFAULT_ALIAS="upload"

# Play's requirements for an upload key, verbatim: RSA, at least 2048 bits,
# valid past 22 October 2033. 10000 days (~27 years) clears that comfortably
# and is the number Google's own documentation uses.
KEY_ALG="RSA"
KEY_SIZE=2048
VALIDITY_DAYS=10000

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_YEL=$'\033[33m'
    C_GRN=$'\033[32m'; C_OFF=$'\033[0m'
else
    C_BOLD=''; C_RED=''; C_YEL=''; C_GRN=''; C_OFF=''
fi

say()  { printf '%s==>%s %s\n' "${C_BOLD}" "${C_OFF}" "$*"; }
ok()   { printf '%s  ok%s %s\n' "${C_GRN}" "${C_OFF}" "$*"; }
die()  { printf '%sfail%s %s\n' "${C_RED}" "${C_OFF}" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Feather Dash -- create the Play upload keystore

USAGE
  packaging/android/make_keystore.sh [path] [--alias NAME] [--help]

  path     where to write the keystore
           (default: ${DEFAULT_KEYSTORE})
  --alias  key alias inside the keystore (default: ${DEFAULT_ALIAS})

It will prompt for the certificate fields and for a passphrase, then print the
environment variables to export before \`build.sh release\`.

It will REFUSE to overwrite an existing keystore. That is not a limitation,
that is the point.
EOF
}

KEYSTORE="${DEFAULT_KEYSTORE}"
ALIAS="${DEFAULT_ALIAS}"
POSITIONAL_SEEN=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --alias)
            shift
            [ $# -gt 0 ] || die "--alias needs a value"
            ALIAS="$1"
            ;;
        -*) die "unknown option: $1  (try --help)" ;;
        *)
            [ -z "${POSITIONAL_SEEN}" ] || die "unexpected argument: $1"
            KEYSTORE="$1"
            POSITIONAL_SEEN="yes"
            ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# refuse to clobber
# ---------------------------------------------------------------------------
if [ -e "${KEYSTORE}" ]; then
    die "a file already exists at:
       ${KEYSTORE}

     Refusing to touch it. If that really is a stale keystore for an app that
     was never published, move it aside yourself -- deliberately:

       mv '${KEYSTORE}' '${KEYSTORE}.bak-\$(date +%Y%m%d)'

     If it is the keystore for a LIVE app, do not move it. Do not regenerate
     it. There is no way to recover from losing it except Play App Signing
     key reset, and that is a support request with a multi-day turnaround."
fi

# ---------------------------------------------------------------------------
# find keytool
# ---------------------------------------------------------------------------
# macOS ships a /usr/bin/keytool STUB that exists, is executable, and fails
# with "Unable to locate a Java Runtime" when there is no JDK. Testing for the
# file is therefore not enough -- it has to be run.
KEYTOOL=""
if command -v keytool >/dev/null 2>&1 && keytool -help >/dev/null 2>&1; then
    KEYTOOL="keytool"
elif [ -n "${JAVA_HOME:-}" ] && [ -x "${JAVA_HOME}/bin/keytool" ]; then
    KEYTOOL="${JAVA_HOME}/bin/keytool"
elif [ -x /usr/libexec/java_home ] && /usr/libexec/java_home >/dev/null 2>&1; then
    KEYTOOL="$(/usr/libexec/java_home)/bin/keytool"
fi

USE_DOCKER=""
if [ -z "${KEYTOOL}" ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        say "no working JDK on this machine -- using the Android build image instead"
        USE_DOCKER="yes"
    else
        die "no usable keytool, and Docker is not running either.

     Pick one:
       brew install --cask temurin         # a real JDK (recommended)
       open -a Docker                      # then re-run; the build image has one
       packaging/android/build.sh image    # build that image first

     (macOS has a /usr/bin/keytool stub that only pretends to exist, which is
     why this check actually runs it rather than just looking for the file.)"
    fi
fi

# ---------------------------------------------------------------------------
# collect the certificate fields
# ---------------------------------------------------------------------------
# These end up in the certificate attached to every build you ever ship. They
# are public. Nothing here is validated by Google, but it is visible to anyone
# who inspects the APK, so it should look like a real developer.
say "certificate details (press enter to accept the default in brackets)"

prompt() {
    local label="$1" default="$2" varname="$3" reply=""
    printf '  %s [%s]: ' "${label}" "${default}"
    IFS= read -r reply || true
    [ -n "${reply}" ] || reply="${default}"
    # eval is the bash-3.2-compatible way to assign to a name held in a
    # variable; the value is single-quoted with embedded quotes escaped.
    eval "${varname}=\$reply"
}

CN=""; OU=""; O=""; L=""; ST=""; C=""
prompt "Your name or studio (CN)" "Varshraj Chouhan" CN
prompt "Organisational unit (OU)" "Games"             OU
prompt "Organisation (O)"         "Varshraj"          O
prompt "City (L)"                 "Indore"            L
prompt "State/Province (ST)"      "Madhya Pradesh"    ST
prompt "Two-letter country (C)"   "IN"                C

DNAME="CN=${CN}, OU=${OU}, O=${O}, L=${L}, ST=${ST}, C=${C}"

printf '\n'
say "about to create:"
printf '  file     %s\n' "${KEYSTORE}"
printf '  alias    %s\n' "${ALIAS}"
printf '  key      %s %s bits, valid %s days\n' "${KEY_ALG}" "${KEY_SIZE}" "${VALIDITY_DAYS}"
printf '  subject  %s\n' "${DNAME}"
printf '\n'
printf 'Proceed? [y/N]: '
IFS= read -r CONFIRM || true
case "${CONFIRM}" in
    y|Y|yes|YES) ;;
    *) die "aborted" ;;
esac

mkdir -p "$(dirname "${KEYSTORE}")"
chmod 700 "$(dirname "${KEYSTORE}")" 2>/dev/null || true

# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
# keytool prompts for the passphrase on its own TTY. That is deliberate: the
# passphrase is never an argument to this script, never in an environment
# variable here, and therefore never in your shell history or in `ps`.
say "keytool will now ask for a passphrase -- use a password manager, 20+ chars"
printf '\n'

if [ -n "${USE_DOCKER}" ]; then
    KEYSTORE_DIR=$(cd -- "$(dirname -- "${KEYSTORE}")" && pwd -P)
    KEYSTORE_FILE=$(basename -- "${KEYSTORE}")
    docker run --rm -it \
        -v "${KEYSTORE_DIR}:/out" \
        -u "$(id -u):$(id -g)" \
        --entrypoint keytool \
        "${FEATHERDASH_IMAGE:-featherdash-android:latest}" \
        -genkeypair \
        -v \
        -keystore "/out/${KEYSTORE_FILE}" \
        -storetype PKCS12 \
        -alias "${ALIAS}" \
        -keyalg "${KEY_ALG}" \
        -keysize "${KEY_SIZE}" \
        -validity "${VALIDITY_DAYS}" \
        -dname "${DNAME}"
else
    # PKCS12 rather than the legacy JKS format: JKS makes keytool emit a
    # migration warning on every single use, and modern JDKs default away
    # from it. gradle signs from PKCS12 happily.
    "${KEYTOOL}" -genkeypair \
        -v \
        -keystore "${KEYSTORE}" \
        -storetype PKCS12 \
        -alias "${ALIAS}" \
        -keyalg "${KEY_ALG}" \
        -keysize "${KEY_SIZE}" \
        -validity "${VALIDITY_DAYS}" \
        -dname "${DNAME}"
fi

[ -f "${KEYSTORE}" ] || die "keytool finished but ${KEYSTORE} does not exist"

# Owner-only. The default umask would often leave it group-readable.
chmod 600 "${KEYSTORE}"
ok "created ${KEYSTORE}"

# ---------------------------------------------------------------------------
# the warning that matters
# ---------------------------------------------------------------------------
cat <<EOF

${C_RED}${C_BOLD}
################################################################################
#                                                                              #
#   READ THIS. IT IS THE MOST IMPORTANT PARAGRAPH IN THIS REPOSITORY.          #
#                                                                              #
################################################################################
${C_OFF}
${C_BOLD}If you lose this file, or forget its passphrase, you can never publish an
update to Feather Dash again.${C_OFF}

Not "it will be difficult". Google cannot sign for you, your users cannot
migrate, and the only path forward is to publish a NEW app under a NEW package
name and ask every existing player to reinstall from scratch, losing their
save, their reviews and their install base.

  file:  ${KEYSTORE}
  alias: ${ALIAS}

${C_YEL}WHAT TO DO, TODAY, BEFORE YOU FORGET:${C_OFF}

  1. ${C_BOLD}Enrol in Play App Signing.${C_OFF} When you upload the first bundle, the Play
     Console offers this. Accept it. Google then holds the real app signing
     key and this file becomes only an UPLOAD key -- and a lost upload key can
     be reset through Play Console support. Without enrolment, this file is
     the app signing key and nothing can save you.

  2. ${C_BOLD}Back the file up in two places that are not this laptop.${C_OFF}
     A password manager's secure-file attachment, plus an encrypted archive in
     cloud storage, is enough. Not a git repository. Not a public one, not a
     private one.

  3. ${C_BOLD}Store the passphrase in your password manager,${C_OFF} in the same entry, with
     the alias name. A keystore whose passphrase lives only in your head is a
     keystore you have already half lost.

  4. ${C_BOLD}Never commit it.${C_OFF} Add these to .gitignore now:
         *.keystore
         *.jks

${C_YEL}TO BUILD A RELEASE${C_OFF} (put these in your password manager, not in .zshrc):

  export FEATHERDASH_KEYSTORE='${KEYSTORE}'
  export FEATHERDASH_KEYSTORE_PASSWORD='<the passphrase you just chose>'
  export FEATHERDASH_KEY_ALIAS='${ALIAS}'
  export FEATHERDASH_KEY_PASSWORD='<the key passphrase, usually the same>'
  packaging/android/build.sh release

${C_YEL}FOR GITHUB ACTIONS${C_OFF} -- create these four repository secrets
(Settings -> Secrets and variables -> Actions):

  ANDROID_KEYSTORE_BASE64     the output of the command below
  ANDROID_KEYSTORE_PASSWORD   the store passphrase
  ANDROID_KEY_ALIAS           ${ALIAS}
  ANDROID_KEY_PASSWORD        the key passphrase

  base64 < '${KEYSTORE}' | tr -d '\\n' | pbcopy

  (that copies it to the clipboard rather than printing it to a terminal that
  might be scrolled back, screen-shared or logged. On Linux use \`xclip -sel c\`.)

EOF

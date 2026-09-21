# moses-env.sh — the shell half of moses_env.py: whose home, where Moses lives, and the operator's
# settings. Sourced, never run:
#
#     . "$(dirname "$(readlink -f "$0")")/lib/moses-env.sh"
#
# Sets MOSES_HOME, MOSES_ROOT, MOSES_CONFIG_DIR, MOSES_MEMORY_DIR, MOSES_STATE, and exports every
# KEY=value in ~/.config/moses/moses.env that the environment does not already set. The environment
# wins, then the settings file, then the caller's own ${X:-default}. Same rules as moses_env.py, and
# moses_env_test.py holds the two to the same answers.
#
# As root, $HOME is /root, never the operator: MOSES_OPERATOR_USER, else whoever ran sudo, else
# /etc/moses/operator. With none of them this stops rather than guessing whose memory to read.
# shellcheck shell=bash

if [ -z "${MOSES_HOME:-}" ]; then
  if [ "$(id -u)" != 0 ]; then
    MOSES_HOME=$HOME
  else
    _moses_op=${MOSES_OPERATOR_USER:-}
    if [ -z "$_moses_op" ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then _moses_op=$SUDO_USER; fi
    if [ -z "$_moses_op" ] && [ -r /etc/moses/operator ]; then _moses_op=$(head -1 /etc/moses/operator | tr -d '[:space:]'); fi
    if [ -z "$_moses_op" ]; then
      # Declared last resort, said aloud: account 1000 (see moses_env.py for why).
      _moses_op=$(getent passwd 1000 | cut -d: -f1)
      if [ -z "$_moses_op" ]; then
        echo "moses-env: running as root with no operator — set MOSES_OPERATOR_USER, run through sudo, or write the user name to /etc/moses/operator" >&2
        return 2 2>/dev/null || exit 2
      fi
      echo "moses-env: no operator declared — using account 1000 ($_moses_op); write it to /etc/moses/operator" >&2
    fi
    MOSES_HOME=$(getent passwd "$_moses_op" | cut -d: -f6)
    unset _moses_op
  fi
fi
export MOSES_HOME
MOSES_CONFIG_DIR=${MOSES_CONFIG_DIR:-$MOSES_HOME/.config/moses}

_moses_settings=${MOSES_SETTINGS:-$MOSES_CONFIG_DIR/moses.env}
if [ -r "$_moses_settings" ]; then
  while IFS= read -r _moses_line || [ -n "$_moses_line" ]; do
    case "$_moses_line" in ''|'#'*) continue ;; esac
    case "$_moses_line" in *=*) ;; *) continue ;; esac
    _moses_k=${_moses_line%%=*}; _moses_k=${_moses_k#export }; _moses_k=${_moses_k//[[:space:]]/}
    _moses_v=${_moses_line#*=}
    case "$_moses_v" in \"*\"|\'*\') _moses_v=${_moses_v:1:${#_moses_v}-2} ;; esac
    case "$_moses_k" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    if [ -z "${!_moses_k+x}" ]; then export "$_moses_k=$_moses_v"; fi
  done < "$_moses_settings"
fi
unset _moses_settings _moses_line _moses_k _moses_v

MOSES_ROOT=${MOSES_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
MOSES_MEMORY_DIR=${MOSES_MEMORY_DIR:-$MOSES_HOME/.claude/memory}
MOSES_STATE=${MOSES_STATE:-$MOSES_HOME/.local/state/moses}
export MOSES_ROOT MOSES_CONFIG_DIR MOSES_MEMORY_DIR MOSES_STATE

# The `claude` CLI — the same order as moses_env.claude_cli(): MOSES_CHAT_CLI, else the per-user install
# at ~/.local/bin/claude if it is there, else whatever PATH finds, else that per-user path (so an error
# names a real place to put it).
if [ -z "${MOSES_CLAUDE_CLI:-}" ]; then
  if [ -n "${MOSES_CHAT_CLI:-}" ]; then MOSES_CLAUDE_CLI=$MOSES_CHAT_CLI
  elif [ -x "$MOSES_HOME/.local/bin/claude" ]; then MOSES_CLAUDE_CLI=$MOSES_HOME/.local/bin/claude
  else MOSES_CLAUDE_CLI=$(command -v claude 2>/dev/null || echo "$MOSES_HOME/.local/bin/claude"); fi
fi
export MOSES_CLAUDE_CLI

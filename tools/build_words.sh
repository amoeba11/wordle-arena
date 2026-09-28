#!/bin/sh
# Rebuilds the word lists embedded in ../index.html.
#   answers: tools/answers.txt (hand-picked common words, space separated)
#   guesses: 5-letter words from macOS /usr/share/dict/words (Webster's 2nd, public domain)
#            plus simple inflections it omits (plurals, -ed/-d, -es) and every answer.
set -e
cd "$(dirname "$0")"
D=/usr/share/dict/words
tr ' ' '\n' < answers.txt | grep -E '^[a-z]{5}$' | sort -u > /tmp/wa_a.txt
{ grep -E '^[a-z]{5}$' $D
  grep -E '^[a-z]{4}$' $D | grep -v 's$' | sed 's/$/s/'
  grep -E '^[a-z]{4}$' $D | grep 'e$' | sed 's/$/d/'
  grep -E '^[a-z]{3}$' $D | sed 's/$/ed/'
  grep -E '^[a-z]{3}$' $D | grep -E '(s|x|z)$' | sed 's/$/es/'
  cat /tmp/wa_a.txt; } | sort -u | comm -23 - /tmp/wa_a.txt > /tmp/wa_v.txt
python3 - <<'PY'
import re
a=" ".join(open("/tmp/wa_a.txt").read().split())
v="".join(open("/tmp/wa_v.txt").read().split())
p="../index.html"; s=open(p).read()
s=re.sub(r'^const ANSWERS_RAW = ".*";$', lambda m: f'const ANSWERS_RAW = "{a}";', s, count=1, flags=re.M)
s=re.sub(r'^const VALID_RAW = ".*";$', lambda m: f'const VALID_RAW = "{v}";', s, count=1, flags=re.M)
open(p,"w").write(s)
print(len(a.split()), "answers,", len(v)//5, "extra guesses")
PY

#!/usr/bin/python3
import json
from argparse import ArgumentParser, FileType
from datetime import datetime
from itertools import permutations, product
from sys import stdin, stdout, stderr, exit as sys_exit
from os.path import isfile, splitext, dirname, abspath, join


# OopCompanion:suppressRename

# Sentinel meaning "-sp was given without a name": use the first preset in the config.
_USE_FIRST_PRESET = "\x00first"
# Sentinel meaning "-af was given without a name": use the first affix set in the config.
_USE_FIRST_AFFIX_SET = "\x00first_affix"


class Logger():

    def __init__(self, file=None, level='DEBUG'):
        self.file = file
        self.accepted_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        self.level = 'DEBUG'
        self.set_level(level)
        self.stdout = stderr

    def _log(self, msg, level='INFO'):
        if self.accepted_levels.index(self.level) <=  self.accepted_levels.index(level):
            msg = f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] ({level}) {msg}\n"
            self.stdout.write(msg)
            if self.file:
                with open(self.file, 'a') as f:
                    f.write(msg)

    def newline(self):
        self.stdout.write('\n')

    def set_level(self, level):
        if not level or level.upper() not in self.accepted_levels:
            self.error(f'Invalid log level "{level}". Keeping {self.level} level')
        else:
            self.level = level.upper()

    def debug(self, *msgs):
        self._log(' '.join([str(x) for x in msgs]), 'DEBUG')

    def info(self, *msgs):
        self._log(' '.join([str(x) for x in msgs]), 'INFO')

    def warning(self, *msgs):
        self._log(' '.join([str(x) for x in msgs]), 'WARNING')

    def error(self, *msgs):
        self._log(' '.join([str(x) for x in msgs]), 'ERROR')



class PasswordDictGenerator():

    # Built-in fallback sign list, used when passgen.json has no "sign_sets" section.
    # Sign sets are normally selected from the config via -ss/--sign-set.
    _SIGNS = ['*','#',"'",'?','¡','¿','!','\\','|','º','ª','"','@','·','$',
              '~','%','&','/','(',')','=','^','[',']','{','}','+','<','>',
              '_','-',';',',','.']

    _WARN_THRESHOLD = 1_000_000   # projected candidates per input word before we warn
    _HARD_CAP = 50_000_000        # projected candidates per input word that we refuse without --force
    _FLUSH_BATCH = 100_000        # candidates buffered before streaming a write when not chunking to files

    # Fallback substitution presets used when no passgen.json is found next to the script.
    # Rules are case-sensitive (note both 'a' and 'A' entries) and may be many-to-one
    # (i/I/l/L -> 1) or one-to-many (a -> 4 or @, s -> 5 or $). The first preset is the default.
    _BUILTIN_PRESETS = {
        "leet": [
            {"from": "a", "to": "4"}, {"from": "e", "to": "3"},
            {"from": "i", "to": "1"}, {"from": "o", "to": "0"},
            {"from": "s", "to": "5"}, {"from": "t", "to": "7"},
        ],
        "extended": [
            {"from": "a", "to": "4"}, {"from": "a", "to": "@"},
            {"from": "A", "to": "4"}, {"from": "A", "to": "@"},
            {"from": "e", "to": "3"}, {"from": "E", "to": "3"},
            {"from": "i", "to": "1"}, {"from": "I", "to": "1"},
            {"from": "l", "to": "1"}, {"from": "L", "to": "1"},
            {"from": "o", "to": "0"}, {"from": "O", "to": "0"},
            {"from": "s", "to": "5"}, {"from": "s", "to": "$"},
            {"from": "S", "to": "5"}, {"from": "S", "to": "$"},
            {"from": "t", "to": "7"}, {"from": "T", "to": "7"},
        ],
    }

    def __init__(self, _input=None, _output=None, year=None, _all=False, dollar=False, at=False, l337=False, _min: int=1, _max: int=200, quiet=False, verbose=False, combine=0, chunk_size=0, force=False, sub_preset=None, sign_set=None, affix_set=None):
        self.input = _input
        self.output_path = _output                          # str path, or None for stdout
        self.output = stdout if _output is None else None   # opened lazily in main()
        self._year = list(year) if year is not None else []
        self.min = _min
        self.max = _max
        self.chunk_size = chunk_size
        self.flags = {
            "all": _all,
            "dollar": dollar,
            "at": at,
            "l337": l337,
            "quiet": quiet,
            "verbose": verbose,
            "combine": combine if combine and combine >= 2 else 0,
            "force": force
        }
        self._psf = None  # per-cased-form decoration factor, computed once in main() after year dedup
        self._config = None               # parsed passgen.json, lazily loaded and cached
        self.logger = Logger(level='DEBUG')
        self.sub_preset_name = None       # name of the active substitution preset, for logging
        self.sub_rules: dict[str, list[str]] = {}  # {from_char: [to_char, ...]}; non-empty enables substitution mode
        self._init_sub_rules(sub_preset)
        self.sign_set_name = None         # name of the active sign set, for logging
        self.signs_list = []              # active list of punctuation signs (set from config or built-in)
        self._init_signs(sign_set)
        self.affix_set_name = None        # name of the active affix set, for logging
        self.affixes_list = []            # active list of number/string affixes; empty = disabled
        self._init_affixes(affix_set)

    def _write_chunk(self, data, chunk_num): #writes a chunk to a numbered output file and returns its path
        stem, ext = splitext(self.output_path)
        path = f"{stem}_{chunk_num:03d}{ext}"
        with open(path, 'w') as f:
            f.write('\n'.join(data) + '\n')
        return path

    def _load_config(self): #parses passgen.json next to the script once (cached); returns {} if absent or invalid
        if self._config is None:
            self._config = {}
            path = join(dirname(abspath(__file__)), 'passgen.json')
            if isfile(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._config = data
                    else:
                        self.logger.warning(f'{path} is not a JSON object; ignoring it.')
                except (json.JSONDecodeError, OSError) as e:
                    self.logger.warning(f'Could not read {path} ({e}); ignoring it.')
        return self._config

    def _presets(self): #the substitution presets from config, or the built-in fallback
        presets = self._load_config().get('presets')
        if isinstance(presets, dict) and presets:
            return presets
        return dict(self._BUILTIN_PRESETS)

    def _sign_sets(self): #the sign sets from config, or a single built-in fallback set
        sign_sets = self._load_config().get('sign_sets')
        if isinstance(sign_sets, dict) and sign_sets:
            return sign_sets
        return {"default": list(self._SIGNS)}

    def _init_sub_rules(self, sub_preset): #builds self.sub_rules from the selected preset (None leaves substitution mode off)
        if sub_preset is None:
            return
        presets = self._presets()
        name = next(iter(presets)) if sub_preset is _USE_FIRST_PRESET else str(sub_preset)
        if name not in presets:
            self.logger.error(f'Unknown sub-preset "{name}". Available: {", ".join(presets)}')
            sys_exit(1)
        rules = {}
        for rule in presets[name]:
            frm, to = rule.get("from"), rule.get("to")
            if not frm or to is None: #need a non-empty source char and a replacement
                continue
            targets = rules.setdefault(frm, [])
            if to not in targets:
                targets.append(to)
        self.sub_rules = rules
        self.sub_preset_name = name

    def _init_signs(self, sign_set): #selects the active sign list (None uses the first set in config / built-in)
        sign_sets = self._sign_sets()
        name = next(iter(sign_sets)) if sign_set is None else sign_set
        if name not in sign_sets:
            self.logger.error(f'Unknown sign-set "{name}". Available: {", ".join(sign_sets)}')
            sys_exit(1)
        chars = [str(c) for c in sign_sets[name] if c != ""] #drop empty entries
        if not chars:
            self.logger.error(f'Sign-set "{name}" is empty.')
            sys_exit(1)
        self.signs_list = chars
        self.sign_set_name = name

    def _affix_sets(self): #the affix sets from config, or a single built-in fallback set
        sets = self._load_config().get('affix_sets')
        if isinstance(sets, dict) and sets:
            return sets
        return {"default": ["1", "12", "123", "1234", "0", "00", "01", "007", "69", "111"]}

    def _init_affixes(self, affix_set): #selects the active affix list (None disables affixes entirely)
        if affix_set is None:
            return
        sets = self._affix_sets()
        name = next(iter(sets)) if affix_set is _USE_FIRST_AFFIX_SET else str(affix_set)
        if name not in sets:
            self.logger.error(f'Unknown affix-set "{name}". Available: {", ".join(sets)}')
            sys_exit(1)
        entries = [str(a) for a in sets[name] if str(a) != ""]
        if not entries:
            self.logger.error(f'Affix-set "{name}" is empty.')
            sys_exit(1)
        self.affixes_list = entries
        self.affix_set_name = name

    def main(self):
        pending = []
        written = 0
        chunks_written = 0
        chunked_to_files = self.chunk_size > 0 and self.output_path is not None
        flush_batch = self.chunk_size if self.chunk_size > 0 else self._FLUSH_BATCH
        if not chunked_to_files and self.output_path:
            self.output = open(self.output_path, 'w')
        try:
            if self.flags["all"]: #check if --all flag has been set
                self._year.append(datetime.now().year) #add the current year to the list
            self._year=list(set(self._year)) #remove duplicates if there's any
            self._psf = self._decoration_factor() #fixed per-cased-form expansion factor for projection
            if not self.flags["quiet"]:
                self.logger.info(f'Sign set "{self.sign_set_name}" active ({len(self.signs_list)} sign(s)).')
            if self.affixes_list and not self.flags["quiet"]:
                self.logger.info(f'Affix set "{self.affix_set_name}" active ({len(self.affixes_list)} affix(es)).')
            if self.sub_rules:
                if not self.flags["quiet"]:
                    self.logger.info(f'Substitution preset "{self.sub_preset_name}" active ({sum(len(v) for v in self.sub_rules.values())} rule(s)).')
                if (self.flags["l337"] or self.flags["all"] or self.flags["dollar"] or self.flags["at"]) and not self.flags["quiet"]:
                    self.logger.warning("Substitution preset active: -l/--all/-d/-at substitutions are disabled to avoid duplication.")
            if self.input == stdin and not self.flags["quiet"]:
                self.logger.info("Insert input, one per line. Finish with a newline plus ctrl+c:")
            lines = set()
            try:
                for line in self.input: #read from choosen input
                    lines.add(line.strip())
            except KeyboardInterrupt: #allows th78e use of Ctrl+C as EOF
                self.logger.newline()
            if not self.flags["quiet"]:
                self.logger.info(f"Loaded {len(lines)} keyword(s).")
            if self.flags["combine"] >= 2 and len(lines) >= 2:
                combined = set()
                for r in range(2, min(self.flags["combine"], len(lines)) + 1):
                    for perm in permutations(lines, r):
                        combined.add(' '.join(perm))
                lines.update(combined)
                if not self.flags["quiet"]:
                    self.logger.info(f"Added {len(combined)} combined keyword(s) ({len(lines)} total).")
            if not self.flags["quiet"]:
                self.logger.info("Generating combinations...")
            for line in lines:
                if self.flags["verbose"]:
                    self.logger.debug(f"Working on: {line}")
                for candidate in self.generate_for_line(line): #stream candidates one at a time
                    if self.min <= len(candidate) <= self.max:
                        pending.append(candidate)
                        if len(pending) >= flush_batch:
                            chunks_written += 1
                            chunk_count = len(pending)
                            fname = ""
                            if chunked_to_files:
                                fname = self._write_chunk(pending, chunks_written)
                            else:
                                self.output.write('\n'.join(pending) + '\n')
                                self.output.flush()
                            pending.clear()
                            written += chunk_count
                            if not self.flags["quiet"]:
                                location = f" to {fname}" if chunked_to_files else ""
                                self.logger.info(f"Chunk {chunks_written} written{location} ({chunk_count} passwords, {written} total so far).")
            if pending:
                if chunked_to_files:
                    chunks_written += 1
                    self._write_chunk(pending, chunks_written)
                else:
                    self.output.write('\n'.join(pending) + '\n')
                written += len(pending)
                pending.clear()
            if not self.flags["quiet"]:
                self.logger.info(f"Total combinations: {written}")
        except KeyboardInterrupt:
            if pending:
                if chunked_to_files:
                    chunks_written += 1
                    self._write_chunk(pending, chunks_written)
                else:
                    self.output.write('\n'.join(pending) + '\n')
                    self.output.flush()
            self.logger.info("\nCatched SIGINT. Exiting...")
            if self.input != stdin:
                self.input.close()
            if not chunked_to_files and self.output != stdout:
                self.output.close()
            sys_exit(0)


    def _decoration_factor(self): #upper-bound estimate of candidates year_signs() produces for one word (projection only)
        s = len(self.signs_list)
        factor = (2 * s) + (s * s)           # year_signs without years: signs + surround
        if self._year:
            yv = 4 * len(self._year)         # year variants per direction (4-digit, reversed, 2-digit, reversed 2-digit)
            factor += 2 * yv                 # bare year_after + year_before
            factor += yv * (2 * s + s * s)   # year_after -> signs + surround
            factor += s * 2 * yv             # signs_after -> year both modes
            factor += yv * (s + s * s)       # year_before -> signs(mode2) + surround
            factor += s * yv                 # signs_before -> year(mode2)
        if self.affixes_list:
            av = len(self.affixes_list)      # one variant per affix per direction
            factor += 2 * av                 # bare affix_after + affix_before
            factor += av * (2 * s + s * s)   # affix_after -> signs + surround
            factor += s * 2 * av             # signs_after -> affix both modes
            factor += av * (s + s * s)       # affix_before -> signs(mode2) + surround
            factor += s * av                 # signs_before -> affix(mode2)
        return factor

    def _cased_forms(self, w): #the distinct cased forms of a seed that get decorated by base()
        forms = [w, w.lower(), w.upper()]
        if not w.istitle() or w.find('_') != -1: #avoid duplicate capitalize() when w is already a single title-cased word
            forms.append(w.capitalize())
        if self.hasVowel(w):
            uv = self.upperVowel(w)
            forms.append(uv)
            if w.capitalize() != uv.swapcase():
                forms.append(uv.swapcase())
        return forms

    def _sub_count(self, word): #number of substitution combinations (including the original) for projection
        n = 1
        for ch in word:
            opts = self.sub_rules.get(ch)
            if opts:
                n *= 1 + len(opts)
        return n

    def apply_subs(self, word): #per-instance, case-sensitive substitutions; yields every changed variant (not the original)
        choices = []
        for ch in word:
            opts = self.sub_rules.get(ch)
            choices.append((ch, *opts) if opts else (ch,))
        for combo in product(*choices):
            cand = ''.join(combo)
            if cand != word:
                yield cand

    def generate_for_line(self, line): #lazily yields every candidate for one input line (duplicates possible by design)
        words = line.split()
        if not words: #skip empty lines
            return
        for _ in range(len(words)):
            w = words.pop(0)
            words.append(w.capitalize())
        w = ''.join(words)
        seeds = {w, w.lower()}
        if len(words) > 1: #multi-word phrase: also offer underscore-joined variants
            joined = '_'.join(words)
            seeds.add(joined)
            seeds.add(joined.lower())
        sub_active = bool(self.sub_rules)
        leet_active = (self.flags["l337"] or self.flags["all"]) and not sub_active
        if leet_active:
            seeds.update(self.f_l337(w))
        elif not sub_active: #dollar/at only when neither leet nor substitution mode is active
            extra = set()
            if self.flags["dollar"]:
                for x in seeds:
                    if 's' in x.lower():
                        extra.add(self.dollar(x))
            if self.flags["at"]:
                for x in seeds:
                    if 'a' in x.lower():
                        extra.add(self.at(x))
            seeds.update(extra)
        decoration = self._psf or self._decoration_factor()
        if sub_active: #substitutions multiply per cased form (case-sensitive), counted across the actual forms
            base_words = sum(self._sub_count(cf) for seed in seeds for cf in self._cased_forms(seed))
        else:
            base_words = sum(1 for seed in seeds for _cf in self._cased_forms(seed))
        projected = base_words * decoration
        if projected > self._HARD_CAP and not self.flags["force"]:
            if not self.flags["quiet"]:
                self.logger.warning(f'Skipping "{line}": projected ~{projected:,} candidates exceed the cap ({self._HARD_CAP:,}). Use --force to override.')
            return
        if projected > self._WARN_THRESHOLD and not self.flags["quiet"]:
            self.logger.warning(f'"{line}" will expand to ~{projected:,} candidates before length filtering.')
        for seed in seeds:
            yield seed
            yield from self.base(seed)

    def base(self, w): #lazily yields the cased + sign/year decorated forms of a seed word
        for cf in self._cased_forms(w):
            yield from self.year_signs(cf)
            if self.sub_rules: #per-instance, case-sensitive substitution variants of this cased form
                for sub in self.apply_subs(cf):
                    yield sub                       # bare substituted word
                    yield from self.year_signs(sub) # and its sign/year decorations

    def hasVowel(self, word): #simple function that returns if a word has a vowel.
        return any(x in word.lower() for x in ('a', 'e', 'i', 'o', 'u'))

    def upperVowel(self, word): #returns the word with all vowels uppercased
        _word = word.lower()
        for char in (x for x in ('a', 'e', 'i', 'o', 'u') if x in _word):
            _word = _word.replace(char, char.upper())
        return _word

    def year_signs(self, w1): #lazily yields combinations of the word with signs and, if specified, the year(s)
        signs_after  = list(self.signs(w1, 1)) #materialized: iterated more than once below
        signs_before = list(self.signs(w1, 2))
        yield from signs_after
        yield from signs_before
        yield from self.surround(w1)
        if self._year:
            year_after  = list(self.year(w1, 1)) #materialized: iterated more than once below
            year_before = list(self.year(w1, 2))
            yield from year_after
            yield from year_before
            for x in year_after:
                yield from self.signs(x, 1) #combination of word+year+sign
                yield from self.signs(x, 2) #combination of sign+word+year
                yield from self.surround(x) #combination of sign+(word+year)+sign
            for x in signs_after:
                yield from self.year(x, 1) #combination of word+sign+year
                yield from self.year(x, 2) #combination of year+word+sign
            for x in year_before:
                yield from self.signs(x, 2) #combination of sign+year+word
                yield from self.surround(x) #combination of sign+(year+word)+sign
            for x in signs_before:
                yield from self.year(x, 2) #combination of year+sign+word
        if self.affixes_list:
            affix_after  = list(self.affix(w1, 1)) #materialized: iterated more than once below
            affix_before = list(self.affix(w1, 2))
            yield from affix_after
            yield from affix_before
            for x in affix_after:
                yield from self.signs(x, 1) #word+affix+sign
                yield from self.signs(x, 2) #sign+word+affix
                yield from self.surround(x) #sign+(word+affix)+sign
            for x in signs_after:
                yield from self.affix(x, 1) #word+sign+affix
                yield from self.affix(x, 2) #affix+word+sign
            for x in affix_before:
                yield from self.signs(x, 2) #sign+affix+word
                yield from self.surround(x) #sign+(affix+word)+sign
            for x in signs_before:
                yield from self.affix(x, 2) #affix+sign+word

    def year(self, word,mode): #lazily yields combinations of the word and the year(s)
        for y in self._year:
            sy = str(y)
            if mode == 1:
                yield word+sy          #combination of word+year with 4 digits
                yield word+sy[::-1]    #combination of word + reversed year
                yield word+sy[2:]      #combination of word+year with 2 digits
                yield word+sy[2:][::-1] #combination of word + reversed year with 2 digits
            else:
                yield sy+word          #combination of year+word with 4 digits
                yield sy[::-1]+word    #combination of reversed year with 4 digits + word
                yield sy[2:]+word      #combination of year with 2 digits + word
                yield sy[2:][::-1]+word #combination of reversed year with 2 digits + word

    def affix(self, word, mode): #lazily yields combinations of the word and short affix strings (e.g. "1", "123", "007")
        for af in self.affixes_list:
            if mode == 1:
                yield f"{word}{af}" #word+affix
            else:
                yield f"{af}{word}" #affix+word

    def signs(self, word, mode): #lazily yields combinations of the word and signs
        for x in self.signs_list:
            if mode == 1:
                yield f"{word}{x}" #combination of word+sign
            else:
                yield f"{x}{word}" #combination of sign+word

    def surround(self, word): #lazily yields the word surrounded by every pair of signs
        for s1 in self.signs_list:
            for s2 in self.signs_list:
                yield f"{s1}{word}{s2}"

    def _generic_sub(self, word, char, rep):
        if word is not None:
            return word.replace(char.lower(), rep).replace(char.upper(), rep)

    def l337_a(self, word): #returns the word with a vowel replaced with 4
        return self._generic_sub(word, "a", "4")

    def l337_e(self, word): #returns the word with e vowel replaced with 3
        return self._generic_sub(word, "e", "3")

    def l337_i(self, word): #returns the word with i vowel replaced with 1
        return self._generic_sub(word, "i", "1")

    def l337_o(self, word): #returns the word with o vowel replaced with 0
        return self._generic_sub(word, "o", "0")

    def l337_s(self, word): #returns the word with s letter replaced with 5
        return self._generic_sub(word, "s", "5")

    def l337_t(self, word): #returns the word with t letter replaced with 7
        return self._generic_sub(word, "t", "7")

    def dollar(self, word): #returns the word with s letter replaced with $
        return self._generic_sub(word, "s", "$")

    def at(self, word): #returns the word with a vowel replaced with @
        return self._generic_sub(word, "a", "@")

    def f_l337(self, word): #returns a set with combinations of the word in a l337 fashion
        l337_list=[]
        word_lower = word.lower()
        if 'a' in word_lower:
            l337_list.append(self.l337_a)
            if self.flags["at"] or self.flags["all"]:
                l337_list.append(self.at)
        if 'e' in word_lower:
            l337_list.append(self.l337_e)
        if 'i' in word_lower:
            l337_list.append(self.l337_i)
        if 'o' in word_lower:
            l337_list.append(self.l337_o)
        if 's' in word_lower:
            l337_list.append(self.l337_s)
            if self.flags["dollar"] or self.flags["all"]:
                l337_list.append(self.dollar)
        if 't' in word_lower:
            l337_list.append(self.l337_t)
        return self.fr_l337(word,l337_list,0)

    def fr_l337(self, w,l_list,j): #recursive function for making combinations of l337 functions of the word
        total=set()
        for i in range(j,len(l_list)):
            word=l_list[i](w)
            if word is not None:
                total.add(word)
                if word != w:  # only recurse if the transformation changed the word
                    total.update(self.fr_l337(word,l_list,i+1))
        return total


def arg_parser():
    parser = ArgumentParser(description="Creates a custom password wordlist from a set of keywords and phrases.")
    parser.add_argument('-i','--input',dest='_input', type=FileType('r'), default=stdin, nargs='?', help='Input file for keywords. If not specified defaults to stdin.')
    parser.add_argument('-o','--output', dest='_output', type=str, default=None, nargs='?', help='Output file path. If not specified, defaults to stdout.')
    parser.add_argument('-y','--year', dest='year', type=int, action='append', default=[], const=datetime.now().year, nargs='?', help='Year for making combinations. Can be specified multiple times. If it\'s specified without value defaults to actual year.')
    parser.add_argument('--all', dest='_all', action='store_true', help='Makes all posible combinations. -y value can be specified normally (by default assumes -y).')
    parser.add_argument('-d','--dollar', dest='dollar', action='store_true', help='Replaces s and S with $.')
    parser.add_argument('-at', dest='at', action='store_true', help='Replaces a and A with @.')
    parser.add_argument('-l','--l337','--l33t', dest='l337', action='store_true', help='Replaces letters with numbers.')
    parser.add_argument('-sp','--sub-preset', dest='sub_preset', nargs='?', const=_USE_FIRST_PRESET, default=None,
                        help='Enable per-instance, case-sensitive substitutions from a preset in passgen.json (or built-in). Without a name, uses the first preset. Mutually exclusive with -l/--all/-d/-at.')
    parser.add_argument('-ss','--sign-set', dest='sign_set', default=None,
                        help='Name of the sign set to use from passgen.json (or built-in). Defaults to the first sign set.')
    parser.add_argument('-af','--affix-set', dest='affix_set', nargs='?', const=_USE_FIRST_AFFIX_SET, default=None,
                        help='Append/prepend short numeric strings from a named affix set in passgen.json and combine them with sign decorations. Without a name, uses the first set.')
    parser.add_argument('-min','--minimum',dest='_min',action='store', type=int, default=1, help='Minimum length of password. Default=1')
    parser.add_argument('-max','--maximum',dest='_max',action='store', type=int, default=200, help='Maximum length of password. Default=200')
    parser.add_argument('-c','--combine', dest='combine', type=int, nargs='?', const=2, default=0,
                        help='Combine input keywords into groups of N words before generating (2 or 3). Default when specified: 2.')
    parser.add_argument('-s','--chunk-size', dest='chunk_size', type=int, nargs='?', const=1000000, default=0,
                        help='When used with -o, write each chunk of N passwords to a separate numbered file. Default when specified: 1000000.')
    parser.add_argument('-f','--force', dest='force', action='store_true', help='Generate even for input words whose projected expansion exceeds the safety cap.')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('-q','--quiet',dest='quiet',action='store_true', help='Suppresses informative output.')
    group.add_argument('-v','--verbose',dest='verbose',action='store_true', help='Adds more informative output.')
    return parser.parse_args()

if __name__ == "__main__":
    args = arg_parser()
    pdg = PasswordDictGenerator(**{k: v for k,v in args._get_kwargs()})
    pdg.main()

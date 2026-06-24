#!/usr/bin/python3
from argparse import ArgumentParser, FileType
from datetime import datetime
from itertools import permutations
from sys import stdin, stdout, stderr, exit as sys_exit
from os.path import isfile, splitext


# OopCompanion:suppressRename


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

    _SIGNS = ['*','#',"'",'?','¡','¿','!','\\','|','º','ª','"','@','·','$',
              '~','%','&','/','(',')','=','^','[',']','{','}','+','<','>',
              '_','-',';',',','.']

    def __init__(self, _input=None, _output=None, year=[], _all=False, dollar=False, at=False, l337=False, _min: int=1, _max: int=200, quiet=False, verbose=False, combine=0, chunk_size=0):
        self.input = _input
        self.output_path = _output                          # str path, or None for stdout
        self.output = stdout if _output is None else None   # opened lazily in main()
        self._year = year
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
            "combine": combine if combine and combine >= 2 else 0
        }
        self.logger = Logger(level='DEBUG')

    def _write_chunk(self, data, chunk_num): #writes a chunk to a numbered output file and returns its path
        stem, ext = splitext(self.output_path)
        path = f"{stem}_{chunk_num:03d}{ext}"
        with open(path, 'w') as f:
            f.write('\n'.join(data) + '\n')
        return path

    def main(self):
        pending = []
        seen = set()
        written = 0
        chunks_written = 0
        chunked_to_files = self.chunk_size > 0 and self.output_path is not None
        if not chunked_to_files and self.output_path:
            self.output = open(self.output_path, 'w')
        try:
            if self.flags["all"]: #check if --all flag has been set
                self._year.append(datetime.now().year) #add the current year to the list
            self._year=list(set(self._year)) #remove duplicates if there's any
            if self.input == stdin and not self.flags["quiet"]:
                self.logger.info("Insert input, one per line. Finish with a newline plus ctrl+c:")
            try:
                lines=set()
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
            result=set()
            total=set()
            for line in lines:
                if self.flags["verbose"]:
                    self.logger.debug(f"Working on: {line}")
                result.clear()
                words = line.split()
                if words: #check for avoid empty lines
                    for i in range(len(words)):
                        w = words.pop(0)
                        words.append(w.capitalize())
                    w = ''.join(words)
                    result.add(w)
                    result.add(w.lower())
                    if len(words) > 1: #check if it's a single word or a sentence
                        result.add('_'.join(words))
                        result.add('_'.join(words).lower())
                    if self.flags["l337"] or self.flags["all"]: #check if l337 or --all flags has been set
                        result.update(self.f_l337(w))
                    total.clear()
                    leet_active = self.flags["l337"] or self.flags["all"]
                    if self.flags["dollar"] and not leet_active: #check if dollar flag has been set (skip if leet handles it)
                        for x in result:
                            if 's' in x.lower():
                                total.add(self.dollar(x))
                    if self.flags["at"] and not leet_active: #check if at flag has been set (skip if leet handles it)
                        for x in result:
                            if 'a' in x.lower():
                                total.add(self.at(x))
                    result.update(total)
                    total.clear()
                    for x in result:
                        total.update(self.base(x))
                    result.update(total)
                new_items = result - seen
                seen.update(new_items)
                pending.extend(x for x in new_items if self.min <= len(x) <= self.max)
                if self.chunk_size > 0 and len(pending) >= self.chunk_size:
                    chunks_written += 1
                    chunk_count = len(pending)
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


    def base(self, w):
        result=self.year_signs(w)
        result.update(self.year_signs(w.lower()))
        result.update(self.year_signs(w.upper()))
        if not w.istitle() or w.find('_') != -1: #avoid duplicate capitalize() when w is already a single title-cased word
            result.update(self.year_signs(w.capitalize()))
        if self.hasVowel(w):
            uv = self.upperVowel(w)
            result.update(self.year_signs(uv))
            if w.capitalize() != uv.swapcase():
                result.update(self.year_signs(uv.swapcase()))
        return result

    def hasVowel(self, word): #simple function that returns if a word has a vowel.
        return any(x in word.lower() for x in ('a', 'e', 'i', 'o', 'u'))

    def upperVowel(self, word): #returns the word with all vowels uppercased
        _word = word.lower()
        for char in (x for x in ('a', 'e', 'i', 'o', 'u') if x in _word):
            _word = _word.replace(char, char.upper())
        return _word

    def year_signs(self, w1): #returns a set with combinations of the word and the signs and, if specified, the year(s)
        result=set()
        signs_after  = self.signs(w1, 1)
        signs_before = self.signs(w1, 2)
        result.update(signs_after)
        result.update(signs_before)
        result.update(self.surround(w1))
        if self._year:
            year_after  = self.year(w1, 1)
            year_before = self.year(w1, 2)
            result.update(year_after)
            result.update(year_before)
            for x in year_after:
                result.update(self.signs(x, 1)) #combination of word+year+sign
                result.update(self.signs(x, 2)) #combination of sign+word+year
                result.update(self.surround(x)) #combination of sign+(word+year)+sign
            for x in signs_after:
                result.update(self.year(x, 1)) #combination of word+sign+year
                result.update(self.year(x, 2)) #combination of year+word+sign
            for x in year_before:
                result.update(self.signs(x, 2)) #combination of sign+year+word
                result.update(self.surround(x)) #combination of sign+(year+word)+sign
            for x in signs_before:
                result.update(self.year(x, 2)) #combination of year+sign+word
        return result

    def year(self, word,mode): #returns a set with combinations of the word and the year(s)
        total=set()
        for y in self._year:
            if mode == 1:
                total.add(word+str(y)) #combination of word+year with 4 digits
                total.add(word+str(y)[::-1]) #combination of word + reversed year
                total.add(word+str(y)[2:])  #combination of word+year with 2 digits
                total.add(word+str(y)[2:][::-1]) #combination of word + reversed year with 2 digits
            else:
                total.add(str(y)+word) #combination of year+word with 4 digits
                total.add(str(y)[::-1]+word) #combination of reversed year with 4 digits + word
                total.add(str(y)[2:]+word) #combination of year with 2 digits + word
                total.add(str(y)[2:][::-1]+word) #combination of reversed year with 2 digits + word
        return total

    def signs(self, word, mode): #returns a set with combinations of the word and signs
        result=set()
        for x in self._SIGNS:
            if mode == 1:
                result.add(f"{word}{x}") #combination of word+sign
            else:
                result.add(f"{x}{word}") #combination of sign+word
        return result

    def surround(self, word): #returns a set with the word surrounded by every pair of signs
        result=set()
        for s1 in self._SIGNS:
            for s2 in self._SIGNS:
                result.add(f"{s1}{word}{s2}")
        return result

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
    parser.add_argument('-min','--minimum',dest='_min',action='store', type=int, default=1, help='Minimum length of password. Default=1')
    parser.add_argument('-max','--maximum',dest='_max',action='store', type=int, default=200, help='Maximum length of password. Default=200')
    parser.add_argument('-c','--combine', dest='combine', type=int, nargs='?', const=2, default=0,
                        help='Combine input keywords into groups of N words before generating (2 or 3). Default when specified: 2.')
    parser.add_argument('-s','--chunk-size', dest='chunk_size', type=int, nargs='?', const=1000000, default=0,
                        help='When used with -o, write each chunk of N passwords to a separate numbered file. Default when specified: 1000000.')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('-q','--quiet',dest='quiet',action='store_true', help='Suppresses informative output.')
    group.add_argument('-v','--verbose',dest='verbose',action='store_true', help='Adds more informative output.')
    return parser.parse_args()

if __name__ == "__main__":
    args = arg_parser()
    pdg = PasswordDictGenerator(**{k: v for k,v in args._get_kwargs()})
    pdg.main()

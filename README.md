# PassGen
PassGen is a fork of https://github.com/tlg-3xs/pass-dict-gen.git/, a tool developed in Python, designed to generate custom password dictionaries from a user-provided list of words and/or phrases.

I started PassGen when I found my old USB drive with a TrueCrypt volume but could not remember the exact password. Fortunately, I can remember a set of phrases I used to generate my passwords at the time: starting from the words in those phrases, I applied various transformations to create a strong password. Unfortunately, the possible combinations are many, and trying each one manually is not feasible.

Here comes PassGen: while looking for a program to generate my personalized dictionary, I found [Pass-Dict-Gen](https://github.com/tlg-3xs/pass-dict-gen.git/) that was almost-but-not-quite what I was looking for. That's why I forked it and began to adapt it to my needs.

## Features

Here are some features of PassGen:
- **Casing variants** — for each keyword, generates lowercase, uppercase, title case, and a vowel-uppercase variant (e.g. `pAssword`) along with its swapcase.
- **Leet speak substitution** (`-l`) — replaces letters with their numeric equivalents: `a→4`, `e→3`, `i→1`, `o→0`, `s→5`, `t→7`. All non-empty subsets of applicable substitutions are applied, generating every combination.
- **Dollar substitution** (`-d`) — replaces `s`/`S` with `$`.
- **At substitution** (`-at`) — replaces `a`/`A` with `@`.
- **Year combinations** (`-y`) — appends and prepends a year (4-digit, 2-digit, and their reverses) to each variant, including combinations with punctuation.
- **Punctuation symbols** — appends and prepends each of 35 punctuation characters to every variant.
- **Multi-word phrases** — input phrases with spaces generate both joined (`word1word2`) and underscore-separated (`word1_word2`) variants.
- **Keyword combination** (`-c`) — combines input keywords into groups of N (default: 2, max: 3), generating all permutations before applying transformations. Useful for assembling candidate phrases from a pool of keywords.
- **Chunked output** (`-s`) — when used with `-o`, writes each batch of N passwords to a separate numbered file (e.g. `wordlist_001.txt`, `wordlist_002.txt`, …). This keeps individual files at a manageable size and allows starting the hashcat steps (see below) with the first chunk while PassGen generates the rest.

## Future Development

Most of the features I need are already in place. I will probably add more transformation functions to generate a wider range of possible passwords, and try to improve efficiency, but do not foresee any revolutionary change in future.

## Usage

PassGen is a command-line program. You can call it with the following arguments:

```
usage: passgen.py [-h] [-i [INPUT]] [-o [OUTPUT]] [-y [YEAR]] [--all] [-d]
                  [-at] [-l] [-min MIN] [-max MAX] [-c [COMBINE]]
                  [-s [CHUNK_SIZE]] [-q | -v]

Creates a custom password wordlist from a set of keywords and phrases.

optional arguments:
  -h, --help            show this help message and exit
  -i [INPUT], --input [INPUT]
                        Input file for keywords. If not specified, defaults to
                        stdin.
  -o [OUTPUT], --output [OUTPUT]
                        Output file. If not specified, defaults to stdout.
  -y [YEAR], --year [YEAR]
                        Year for making combinations. Can be specified
                        multiple times. If specified without a value,
                        defaults to the current year.
  --all                 Makes all possible combinations. -y value can be
                        specified normally (by default assumes -y).
  -d, --dollar          Replaces s and S with $.
  -at                   Replaces a and A with @.
  -l, --l337, --l33t    Replaces letters with numbers.
  -min MIN, --minimum MIN
                        Minimum length of password. Default=1
  -max MAX, --maximum MAX
                        Maximum length of password. Default=200
  -c [COMBINE], --combine [COMBINE]
                        Combine input keywords into groups of N words before
                        generating (2 or 3). Default when specified: 2.
  -s [CHUNK_SIZE], --chunk-size [CHUNK_SIZE]
                        When used with -o, write each chunk of N passwords to
                        a separate numbered file. Default when specified:
                        1000000.
  -q, --quiet           Suppresses informative output.
  -v, --verbose         Adds more informative output.
```

### Use with hashcat

The idea of PassGen is to generate one personalized dictionary to be used with hashcat to find a working password for an old TrueCrypt volume.

In principle, the generated dictionary can be used with any algorithm supported by hashcat.


#### Step 1: Extract the volume header

For a file container:

``dd if=volume.tc bs=512 count=1 of=header.bin``

For a raw disk/partition (e.g., \\.\PhysicalDrive1 on Windows):

``dd if=\\.\PhysicalDrive1 bs=512 count=1 of=header.bin``

#### Step 2: Generate your wordlist

Prepare a text file (e.g. keywords.txt) with the list of words/phrases to be transformed and remixed to create the password dictionary.

``python passgen.py -i keywords.txt --all -o wordlist.txt``

If you have a pool of separate keywords that you want to combine into phrases before generating, use `-c`:

``python passgen.py -i keywords.txt --all -c 2 -o wordlist.txt``

For very large wordlists, use `-s` to split the output into separate numbered files (e.g. `wordlist_001.txt`, `wordlist_002.txt`, …):

``python passgen.py -i keywords.txt --all -s -o wordlist.txt``


#### Step 3: Run hashcat

TrueCrypt/VeraCrypt allow a range of algorithms for hashing and encryption.
If you don't know the algorithm used in your case, run all the modes. RIPEMD160 + AES was the TrueCrypt GUI default, so start there.


Most likely for old volumes (TrueCrypt default - file container):
``hashcat -m 6211 header.bin wordlist.txt``

TrueCrypt default - system/boot partition:
``hashcat -m 6241 header.bin wordlist.txt``

If those fail, try SHA512 and Whirlpool variants:
``hashcat -m 6221 header.bin wordlist.txt``
``hashcat -m 6231 header.bin wordlist.txt``

If it is a VeraCrypt volume instead (modes 137xx):
``hashcat -m 13711 header.bin wordlist.txt  # RIPEMD160``
``hashcat -m 13721 header.bin wordlist.txt  # SHA512``
``hashcat -m 13751 header.bin wordlist.txt  # SHA256 (newer VeraCrypt default)``

Practical notes:
- TrueCrypt is slow to crack — PBKDF2 with 1000 iterations for containers, 2000 for boot. VeraCrypt is much slower (500,000 iterations). A GPU makes a significant difference.
- If you have any memory of the password structure (length, used a word + year, etc.), add `-min`/`-max` to cut the wordlist size.
- hashcat's `--status` flag shows progress and estimated time to completion.
- The header-only approach is safe — you're not touching the actual encrypted data on the volume.

## Automating the search with passtest

I found that the wordlists generated with PassGen may easily explode in milions of variations, and running hashcat by hand against each chunk and each mode gets tedious fast, especially when PassGen is still streaming new `wordlist_###.txt` files with `-s`.

In my lziness, I came with `passtest.py` to automates the whole loop:

It reads a `passtest.json` configuration file (next to the script, or passed with `-c`) that specifies at least:
- `dict_dir` — the directory to monitor for wordlist chunks;
- `wordlist` — the base name, so it watches for `wordlist_###.txt`;
- `header_bin` — the volume header extracted in Step 1;
- `hcat_path` — the path to the hashcat executable;
- a list of hashcat jobs (each with its `mode`, an optional GPU `device`, and optional extra `args`).

What it does:
1. **Monitors** `dict_dir` for `wordlist_###.txt` files (skipping ones already marked `DONE_`), oldest first.
2. For each chunk it **runs hashcat** once per configured job against `header_bin`. Jobs pinned to different GPUs run **in parallel** (one hashcat instance per device); jobs sharing a device run sequentially.
3. If a password is **found**, it prints it to stdout, records it in the found file, and exits.
4. Otherwise it marks the chunk done by renaming it `DONE_wordlist_###.txt` and moves on to the next. When no chunk is left it keeps polling, so you can start `passtest` as soon as PassGen writes its first chunk and let the two run side by side.

Example: start PassGen streaming chunks, then start the watcher:

``python passgen.py -i keywords.txt --all -s -o wordlists/wordlist.txt``

``python passtest.py``

A minimal `passtest.json` for a VeraCrypt volume across three GPUs:

```json
{
  "dict_dir": "wordlists",
  "wordlist": "wordlist",
  "header_bin": "header.bin",
  "hcat_path": "hashcat",
  "jobs": [
    {"mode": 13711, "device": 1, "args": []},
    {"mode": 13721, "device": 2, "args": []},
    {"mode": 13751, "device": 3, "args": ["-O"]}
  ]
}
```

Notes:
- Running several hashcat instances at once only helps when each targets a **different physical GPU** (`device`). On a single GPU the instances just contend for it, so give every job the same (or no) `device` and they run one after another.
- The example mode numbers and device ids are placeholders — set them to your volume's actual hashing algorithm and your real GPU ids (`hashcat -I` lists devices).
- passtest runs on both Windows and Linux and uses only the Python 3 standard library.


## Disclaimer

This project was created for personal use, to recover the password of an owned USB drive.

I consider any password generated with this tool too weak to protect anything important or precious, so I presume PassGen is only useful in cases like mine or at most to assess the strength of a password.

Therefore, I hope this software can be useful to anybody involved in legal pentesting or ethical hacking, and I do not endorse nor encourage any illegal or malicious use of it, because it is pointless from any point of view.

Bottom line: if you use it to access something you do not have the right to, either you are wasting your time and won't succeed, or you will succeed but it won't be worth the risk. In both cases you are likely breaking some laws — you are the only person responsible for your actions, and I am not liable for any damage or legal consequence that may result from the use of this software.

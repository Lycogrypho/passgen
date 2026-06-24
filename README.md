# passgen
PassGen is a fork of https://github.com/tlg-3xs/pass-dict-gen.git/

Pass-Dict-Gen is a tool developed in Python, designed to generate custom password dictionaries derived from a user-provided list of words and/or phrases. The current functionality of this utility encompasses the following features:
- Substitution of certain letters with their numeric equivalents, enhancing the complexity of the resultant passwords.
- Replacement of the letters 's' and 'S' with the dollar symbol ('$'), further diversifying the password pool.
- Substitution of the letters 'a' and 'A' with the '@' symbol, adding an additional layer of complexity.
- Generation of password combinations incorporating the current year or a user-specified year, providing a temporal aspect to the password creation process.
- Creation of password combinations that include punctuation symbols, thereby increasing the potential password permutations.

Future Development:
I'm currently in the process of refining the codebase to enhance readability and maintainability, ensuring that Pass-Dict-Gen remains a robust and user-friendly tool for password dictionary generation.
```
usage: passgen.py [-h] [-i [INPUT]] [-o [OUTPUT]] [-y [YEAR]] [--all] [-d]
                  [-at] [-l] [-min MIN] [-max MAX] [-q | -v]

Creates a custom password wordlist from a set of keywords and phrases.

optional arguments:
  -h, --help            show this help message and exit
  -i [INPUT], --input [INPUT]
                        Input file for keywords. If not specified defaults to
                        stdin.
  -o [OUTPUT], --output [OUTPUT]
                        Output file. If not specified defaults to stdout.
  -y [YEAR], --year [YEAR]
                        Year for making combinations. Can be specified
                        multiple times. If it's specified without value
                        defaults to actual year.
  --all                 Makes all posible combinations. -y value can be
                        specified normally (by default assumes -y).
  -d, --dollar          Replaces s and S with $.
  -at                   Replaces a and A with @.
  -l, --l337, --l33t    Replaces letters with numbers.
  -min MIN, --minimum MIN
                        Minimum length of password. Default=1
  -max MAX, --maximum MAX
                        Maximum length of password. Default=200
  -q, --quiet           Suppresses informative output.
  -v, --verbose         Adds more informative output.
```

## Use with ''hascat''


### Extract the volume header

For a file container:
``dd if=volume.tc bs=512 count=1 of=header.bin``
For a raw disk/partition (e.g., \\.\PhysicalDrive1 on Windows):
``dd if=\\.\PhysicalDrive1 bs=512 count=1 of=header.bin``

### Generate your wordlist

python passgen.py -i keywords.txt --all -o wordlist.txt

### Run hashcat

Since you don't know the algorithm, run all TrueCrypt modes. RIPEMD160 + AES was the TrueCrypt GUI default, so start there:

Most likely for old volumes (TrueCrypt default - file container)
``hashcat -m 6211 header.bin wordlist.txt``

TrueCrypt default - system/boot partition
``hashcat -m 6241 header.bin wordlist.txt``

If those fail, try SHA512 and Whirlpool variants
``hashcat -m 6221 header.bin wordlist.txt``
``hashcat -m 6231 header.bin wordlist.txt``

If it is a VeraCrypt volume instead (modes 137xx):
``hashcat -m 13711 header.bin wordlist.txt  # RIPEMD160``
``hashcat -m 13721 header.bin wordlist.txt  # SHA512``
``hashcat -m 13751 header.bin wordlist.txt  # SHA256 (newer VeraCrypt default)``

Practical notes
- TrueCrypt is slow to crack — PBKDF2 with 1000 iterations for containers, 2000 for boot. VeraCrypt is much slower (500,000 iterations). A GPU makes a significant difference.
- If you have any memory of the password structure (length, used a word + year, etc.), add -min/-max to passgen.py to cut wordlist size.
- hashcat's --status flag shows progress and estimated time to completion.
- The header-only approach is safe — you're not touching the actual encrypted data on the volume.

## Disclaimer
This project is designed for the purpose of enhancing my personal understanding of Python and is intended to be used for auditing the strength of one's own passwords. It is not designed or intended to be used for any illegal activities, including unauthorized access to systems or data. The creator of this project does not condone such misuse and will not be held responsible for any damages or legal consequences resulting from such activities. Users are advised to use this tool responsibly and in compliance with all applicable laws and regulations.

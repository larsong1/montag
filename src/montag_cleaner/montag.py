#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import argparse
import subprocess
import re
import tempfile

try:
    # Aspose.Words for Python via .NET (used for EPUB <-> MOBI conversions)
    import aspose.words as aw  # type: ignore
    _ASPOSE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _ASPOSE_AVAILABLE = False

import magic
import ebooklib

from ebooklib import epub
from tempfile import gettempdir

textSplitRegex = re.compile(r'\w+|\W+', re.DOTALL | re.MULTILINE | re.U)


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def tagTokenizer(s):  # yields (returns) strNeedsCensoring, str
    inTag = False
    lastChar = None
    tokenPosStart = 0
    lastYieldEnd = -1
    for i, char in enumerate(s):
        if inTag and (char == '>') and (lastChar != '\\'):
            # we are in an HTML tag, no need to censor anything
            inTag = False
            if i >= tokenPosStart:
                # eprint(f"TAG: {s[tokenPosStart:i+1]}")
                yield False, s[tokenPosStart : i + 1]
                lastYieldEnd = i
            tokenPosStart = i + 1
        elif (not inTag) and (char == '<') and (lastChar != '\\'):
            # we are not in an HTML tag, split up words/non-words and
            # only censor the words
            inTag = True
            if i > tokenPosStart:
                for textToken in re.findall(textSplitRegex, s[tokenPosStart:i]):
                    # eprint(f"TXT: {textToken}")
                    yield ((len(textToken) > 2) and textToken[:1].isalpha()), textToken
                lastYieldEnd = i - 1
            tokenPosStart = i
        lastChar = char

    if len(s) > lastYieldEnd:
        if inTag:
            yield False, s[lastYieldEnd + 1 : len(s)]
        else:
            for textToken in re.findall(textSplitRegex, s[lastYieldEnd + 1 : len(s)]):
                yield ((len(textToken) > 2) and textToken[:1].isalpha()), textToken


def RunMontag():
    devnull = open(os.devnull, 'w')

    parser = argparse.ArgumentParser(
        description='e-book profanity scrubber', add_help=False, usage=f'{os.path.basename(__file__)} [options]'
    )
    requiredNamed = parser.add_argument_group('required arguments')
    requiredNamed.add_argument(
        '-i', '--input', required=True, dest='input', metavar='<STR>', type=str, default='', help='Input file'
    )
    requiredNamed.add_argument(
        '-o', '--output', required=True, dest='output', metavar='<STR>', type=str, default='', help='Output file'
    )
    requiredNamed.add_argument(
        '-f',
        '--output-format',
        dest='outfmt',
        metavar='<STR>',
        type=str,
        choices=['epub', 'mobi'],
        default=None,
        help='Output format: epub or mobi (default: inferred from --output extension)',
    )
    requiredNamed.add_argument(
        '-w',
        '--word-list',
        dest='swears',
        metavar='<STR>',
        type=str,
        default=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'swears.txt'),
        help='Profanity list text file (default: swears.txt)',
    )
    requiredNamed.add_argument(
        '-e',
        '--encoding',
        dest='encoding',
        metavar='<STR>',
        type=str,
        default='utf-8',
        help='Text encoding (default: utf-8)',
    )
    try:
        parser.error = parser.exit
        args = parser.parse_args()
    except SystemExit:
        parser.print_help()
        exit(2)

    # infer/validate output format and normalize output filename extension
    out_ext = os.path.splitext(args.output)[1].lower().lstrip('.')
    if args.outfmt is None:
        if out_ext in ('epub', 'mobi'):
            args.outfmt = out_ext
        else:
            # default to epub if not specified and extension unknown
            args.outfmt = 'epub'
            # if user passed a path without extension, append one
            if out_ext == '':
                args.output = args.output + '.epub'
    else:
        # ensure output filename matches requested format
        if out_ext not in ('epub', 'mobi'):
            # add extension
            args.output = args.output + f'.{args.outfmt}'
        elif out_ext != args.outfmt:
            # replace extension
            base = os.path.splitext(args.output)[0]
            args.output = base + f'.{args.outfmt}'

    # initialize the set of profanity
    swears = set(map(lambda x: x.lower(), [line.strip() for line in open(args.swears, 'r', encoding=args.encoding)]))

    # determine the type of the ebook
    bookMagic = magic.from_file(args.input, mime=True)

    eprint(f'Processing "{args.input}" of type "{"".join(bookMagic)}"')

    with tempfile.TemporaryDirectory() as tmpDirName:
        metadataFileSpec = os.path.join(tmpDirName, 'metadata.opf')

        # save off the metadata to be restored after conversion
        eprint("Extracting metadata...")
        metadataExitCode = subprocess.call(
            ["ebook-meta", args.input, "--to-opf=" + metadataFileSpec], stdout=devnull, stderr=devnull
        )
        if metadataExitCode != 0:
            raise subprocess.CalledProcessError(
                metadataExitCode, f"ebook-meta {args.input} --to-opf={metadataFileSpec}"
            )

        # convert the book from whatever format it is into EPUB for processing
        def _convert_any_to_epub(input_path: str, output_epub_path: str) -> None:
            """Convert any supported input (MOBI/EPUB/etc.) to EPUB using Aspose if available; otherwise fallback to Calibre."""
            nonlocal devnull
            if _ASPOSE_AVAILABLE:
                eprint("Converting to EPUB with Aspose.Words...")
                doc = aw.Document(input_path)
                doc.save(output_epub_path, aw.SaveFormat.EPUB)
            else:
                eprint("Converting to EPUB with Calibre (Aspose not available)...")
                exit_code = subprocess.call(["ebook-convert", input_path, output_epub_path], stdout=devnull, stderr=devnull)
                if exit_code != 0:
                    raise subprocess.CalledProcessError(exit_code, f"ebook-convert {input_path} {output_epub_path}")

        # Decide if we need conversion to EPUB
        if "epub" in bookMagic.lower():
            wasEpub = True
            # Keep original EPUB for processing; Aspose round-trip happens after processing
            epubFileSpec = args.input
        else:
            wasEpub = False
            epubFileSpec = os.path.join(tmpDirName, 'ebook.epub')
            _convert_any_to_epub(args.input, epubFileSpec)

        # todo: somehow links/TOCs tend to get messed up

        eprint("Processing book contents...")
        book = epub.read_epub(epubFileSpec)
        newBook = epub.EpubBook()
        newBook.spine = ['nav']
        documentNumber = 0
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                documentNumber += 1
                cleanTokens = []
                for tokenNeedsCensoring, token in tagTokenizer(item.get_content().decode(args.encoding)):
                    if tokenNeedsCensoring and (token.lower() in swears):
                        # print(f"censoring:→{token}←")
                        cleanTokens.append("*" * len(token))
                    else:
                        # print(f"including:→{token}←")
                        cleanTokens.append(token)
                    # if (len(cleanTokens) % 100 == 0):
                    #   eprint(f"Processed {len(cleanTokens)} tokens from section {documentNumber}...")
                item.set_content(''.join(cleanTokens).encode(args.encoding))
                newBook.spine.append(item)
                newBook.add_item(item)
            else:
                newBook.add_item(item)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        # write EPUB (either final or intermediate)
        eprint("Generating output...")
        cleanEpubFileSpec = os.path.join(tmpDirName, 'ebook_cleaned.epub')
        epub.write_epub(cleanEpubFileSpec, newBook)
        if args.outfmt == 'epub':
            # Convert cleaned EPUB back to EPUB using Aspose when available
            if _ASPOSE_AVAILABLE and wasEpub:
                # Do a round-trip via MOBI then back to EPUB, as requested
                eprint("Finalizing EPUB via MOBI round-trip with Aspose.Words...")
                tmpMobi2 = os.path.join(tmpDirName, 'ebook_cleaned.mobi')
                doc_rt_in = aw.Document(cleanEpubFileSpec)
                doc_rt_in.save(tmpMobi2, aw.SaveFormat.MOBI)
                doc_rt_mid = aw.Document(tmpMobi2)
                doc_rt_mid.save(args.output, aw.SaveFormat.EPUB)
            elif _ASPOSE_AVAILABLE:
                eprint("Finalizing EPUB with Aspose.Words...")
                doc_final = aw.Document(cleanEpubFileSpec)
                doc_final.save(args.output, aw.SaveFormat.EPUB)
            else:
                # Fallback: move/write the cleaned EPUB directly
                eprint("Finalizing EPUB without Aspose (direct write)...")
                # Write directly to requested output path
                # Re-write to ensure path; alternatively, copy file content
                if cleanEpubFileSpec != args.output:
                    # Use Calibre to ensure minimal normalization if available, else copy
                    try:
                        fromEpubExitCode = subprocess.call(
                            ["ebook-convert", cleanEpubFileSpec, args.output], stdout=devnull, stderr=devnull
                        )
                        if fromEpubExitCode != 0:
                            raise subprocess.CalledProcessError(fromEpubExitCode, f"ebook-convert {cleanEpubFileSpec} {args.output}")
                    except FileNotFoundError:
                        # As a last resort, copy bytes
                        with open(cleanEpubFileSpec, 'rb') as src_f, open(args.output, 'wb') as dst_f:
                            dst_f.write(src_f.read())
        else:
            eprint("Converting cleaned EPUB to desired format...")
            # Convert cleaned EPUB to the requested output format
            if args.outfmt == 'mobi':
                if _ASPOSE_AVAILABLE:
                    doc_out = aw.Document(cleanEpubFileSpec)
                    doc_out.save(args.output, aw.SaveFormat.MOBI)
                else:
                    fromEpubExitCode = subprocess.call(
                        ["ebook-convert", cleanEpubFileSpec, args.output], stdout=devnull, stderr=devnull
                    )
                    if fromEpubExitCode != 0:
                        raise subprocess.CalledProcessError(fromEpubExitCode, f"ebook-convert {cleanEpubFileSpec} {args.output}")
            else:
                raise ValueError(f"Unsupported output format: {args.outfmt}")

        # restore metadata
        eprint("Restoring metadata...")
        metadataExitCode = subprocess.call(
            ["ebook-meta", args.output, "--from-opf=" + metadataFileSpec], stdout=devnull, stderr=devnull
        )
        if metadataExitCode != 0:
            raise subprocess.CalledProcessError(
                metadataExitCode, f"ebook-meta {args.output} --from-opf={metadataFileSpec}"
            )


if __name__ == '__main__':
    RunMontag()

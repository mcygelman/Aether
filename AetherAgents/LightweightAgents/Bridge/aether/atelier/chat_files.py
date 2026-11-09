"""Own bounded Chat file access, including a server-bound personal scratchpad.

Never execute shell commands, access peer scratchpads or bypass file/link checks.
"""
from collections import Counter
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from aether.integrity.sealbox import atomic_write_bytes
from .document_text import DOCUMENT_TYPES, IMAGE_TYPES, MAX_DOCUMENT, extract

MAX_BYTES = 256 * 1024
MAX_FILES = 1000
TEXT_TYPES = set('.txt .md .markdown .rst .csv .tsv .json .jsonl .yaml .yml .toml .xml .html .htm .css .js .mjs .cjs .ts .tsx .jsx .py .sql .sh .ps1 .bat .c .h .cpp .hpp .cs .java .go .rs .rb .php .tex .ini .cfg .log'.split())
READ_TYPES = TEXT_TYPES | DOCUMENT_TYPES | IMAGE_TYPES | {'.doc'}
EXCLUDED = {'node_modules', '__pycache__', 'venv', 'env', 'build', 'dist', 'target',
            'credentials', 'operators', 'agentprofiles', 'aetherteam', 'aethermemory',
            'memory_fabric', 'memory', 'minds', 'identities'}
SECRET_NAME = re.compile(r'(^|[._-])(secrets?|passwords?|credentials?|tokens?)([._-]|$)', re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def excluded(name):
    return name.startswith('.') or name.casefold() in EXCLUDED or bool(SECRET_NAME.search(name))


def direct(path, *, missing=False, trusted_root=None):
    """Reject links/junctions at every existing component, not just the leaf."""
    for part in reversed((path, *path.parents)):
        if part.name and (part.name.rstrip(' .')!=part.name or any(c in part.name for c in ':*?"<>|')):
            raise ValueError('Use a normal file or folder name without aliases or device syntax.')
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing and part == path:
                continue
            raise ValueError('The selected path no longer exists.')
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked folders and files are outside Chat file access.')
        trusted_ancestor = trusted_root is not None and (part == trusted_root or part in trusted_root.parents)
        if part.name and excluded(part.name) and not trusted_ancestor:
            raise ValueError('Hidden, credential and agent-memory locations are excluded.')
    return path


class ChatFiles:
    def __init__(self, folder, cache, protected=(), *, scratchpad=None):
        raw = folder.get('root', '') if isinstance(folder, dict) else ''
        if not isinstance(raw, str) or not raw or raw.startswith(('\\\\', '//')):
            raise ValueError('Choose a local folder first.')
        self.root = Path(raw)
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise ValueError('Enter an absolute local folder path.')
        self.scratchpad = Path(scratchpad).absolute() if scratchpad is not None else None
        trusted = self.scratchpad if self.scratchpad is not None and (self.root == self.scratchpad or self.scratchpad in self.root.parents) else None
        direct(self.root, trusted_root=trusted)
        self.trusted_root = trusted
        self.root = self.root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Choose a folder, not a file.')
        self.protected = [Path(p).resolve() for p in protected if p]
        self.check_protected(self.root)
        self.folder = folder
        self.cache = Path(cache) / (digest(str(self.root).encode()) + '.json')
        if os.name == 'nt':
            # Index staging names can exceed MAX_PATH inside an installed operator store.
            self.cache = Path('\\\\?\\' + str(self.cache.absolute()))

    def check_protected(self, path):
        if any((path == p or p in path.parents) and not
               (self.trusted_root is not None and (p == self.trusted_root or p in self.trusted_root.parents))
               for p in self.protected):
            raise ValueError('Aether application data and agent homes are outside Chat file access.')

    def path(self, relative='', *, missing=False):
        if not isinstance(relative, str) or len(relative) > 1024 or '\x00' in relative:
            raise ValueError('Use a relative file path within the selected folder.')
        value = relative.replace('\\', '/')
        # Accept pasted absolute paths only inside this exact granted folder.
        absolute = Path(value)
        if absolute.is_absolute():
            try:
                value = absolute.relative_to(self.root).as_posix()
            except ValueError:
                raise ValueError('That path is outside the permitted folder contents.') from None
            if value == '.': value = ''
        parts = value.split('/') if value else []
        if value.startswith('/') or any(p in ('', '.', '..') or ':' in p or excluded(p) for p in parts):
            raise ValueError('That path is outside the permitted folder contents.')
        path = self.root.joinpath(*parts)
        direct(path, missing=missing, trusted_root=self.trusted_root)
        self.check_protected(path)
        if self.root != path and self.root not in path.resolve(strict=not missing).parents:
            raise ValueError('That path leaves the selected folder.')
        return path

    def relative(self, path):
        return path.relative_to(self.root).as_posix()

    def bytes(self, relative):
        path = self.path(relative)
        suffix = path.suffix.casefold()
        if suffix == '.doc':
            raise ValueError('Legacy .doc files need to be saved as .docx in Word before Chat can read them.')
        if suffix not in READ_TYPES or not path.is_file():
            raise ValueError('Choose a supported text, PDF, Word or image file.')
        maximum = MAX_DOCUMENT if suffix in DOCUMENT_TYPES | IMAGE_TYPES else MAX_BYTES
        before = path.lstat()
        if before.st_nlink != 1 or before.st_size > maximum:
            raise ValueError('Files must not be hard-linked; text is limited to 256 KiB and documents/images to 20 MiB.')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError('The file changed while opening. Try again.')
            self.path(relative)
            raw = stream.read(maximum + 1)
        if len(raw)>maximum:
            raise ValueError('The file exceeds its reading limit.')
        return raw

    def document(self, relative, page=None):
        raw = self.bytes(relative)
        suffix = Path(relative).suffix.casefold()
        if suffix in DOCUMENT_TYPES | IMAGE_TYPES:
            return raw, extract(raw,suffix,page)
        if b'\0' in raw:
            raise ValueError('This is not a supported text document.')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('This file is not UTF-8 text.') from None
        return raw, dict(text=text,format='text',sections=[],warnings=[],page_count=0)

    def raw(self, relative):
        raw, document = self.document(relative)
        return raw, document['text']

    @staticmethod
    def locations(document, start, end):
        return [s['label'] for s in document['sections'] if s['start']<=end and s['end']>=start][:20]

    def read(self, relative, start=1, count=100, page=None, *, include_preview=False):
        relative = self.relative(self.path(relative))
        if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 200:
            raise ValueError('Read 1–200 lines, starting at line 1 or later.')
        if page is not None and (type(page) is not int or page<1):
            raise ValueError('Choose a PDF or TIFF page from this document.')
        raw, document = self.document(relative,page)
        lines = document['text'].splitlines()
        if page is not None:
            if document['format'] not in ('pdf','image') or not 1<=page<=document['page_count']:
                raise ValueError('Choose a PDF or TIFF page from this document.')
            section = next((s for s in document['sections'] if s.get('page')==page),None)
            if section is None and document['format']=='pdf':
                raise ValueError('OCR found no readable text on this PDF page. Try a clearer scan or check the installed Windows OCR language.')
            if section:
                start=max(start,section['start']);count=min(count,max(0,section['end']-start+1))
        selected=lines[start-1:start-1+count]
        if document['format']!='text':
            while len('\n'.join(selected))>24000:
                selected.pop()
            count=len(selected)
        chunk = '\n'.join(selected)
        if len(chunk) > 24000:
            raise ValueError('This line range is too large. Read fewer lines.')
        result = dict(path=relative, start=start if lines else 0, end=min(len(lines), start+count-1),
                    total_lines=len(lines), sha256=digest(raw), text=chunk,format=document['format'],
                    page_count=document['page_count'],warnings=document['warnings'],
                    locations=self.locations(document,start,start+count-1))
        if document['format']=='image':
            result.update(width=document['width'],height=document['height'],frames=document['frames'],preview_page=document['preview_page'],selected_page=page)
            if document['page_count']:
                result.update(blank_pages=document['blank_pages'],unread_pages=document['unread_pages'])
                if page and not result['locations']:result['locations']=['TIFF page '+str(page)+' (OCR)']
            if include_preview:result['preview']=document['preview']
        return result

    def listing(self, relative=''):
        path = self.path(relative)
        relative = '' if path == self.root else self.relative(path)
        if not path.is_dir():
            raise ValueError('Choose a folder to browse.')
        rows, skipped = [], 0
        with os.scandir(path) as entries:
            for entry in entries:
                if len(rows)>=200 or len(rows) + skipped >= 2000:
                    return dict(path=relative, entries=sorted(rows, key=lambda r:(not r['directory'],r['name'].lower())), truncated=True)
                try:
                    p = self.path(self.relative(Path(entry.path)))
                    directory = p.is_dir()
                    if not directory and (p.suffix.casefold() not in READ_TYPES or p.stat().st_nlink != 1):
                        skipped += 1
                        continue
                    rows.append(dict(name=p.name, path=self.relative(p), directory=directory,
                                     bytes=0 if directory else p.stat().st_size))
                except (OSError, ValueError):
                    skipped += 1
        return dict(path=relative, entries=sorted(rows, key=lambda r:(not r['directory'],r['name'].lower())), truncated=False)

    def files(self):
        self.scan_limited = False
        stack, visited, directories = [(self.root, 0)], 0, 0
        while stack and visited < MAX_FILES and directories < 200:
            directory, depth = stack.pop()
            directories += 1
            try:
                listing = self.listing(self.relative(directory) if directory != self.root else '')
            except (ValueError, OSError):
                continue
            self.scan_limited = self.scan_limited or listing['truncated']
            for item in listing['entries']:
                if item['directory']:
                    if depth < 12:
                        stack.append((self.root / item['path'], depth+1))
                else:
                    visited += 1
                    yield item['path']
                    if visited >= MAX_FILES:
                        break
        self.scan_limited = self.scan_limited or bool(stack) or visited>=MAX_FILES

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError('Enter a search phrase up to 200 characters.')
        hits, scanned, total, skipped = [], 0, 0, []
        deadline=time.monotonic()+20
        for relative in self.files():
            if time.monotonic()>deadline:
                self.scan_limited=True;break
            try:
                raw, document = self.document(relative)
                text=document['text']
                if document.get('partial'):
                    skipped.append(dict(path=relative,reason='This document/image was only partly read. Open its preview for details.'))
            except (ValueError, OSError) as exc:
                skipped.append(dict(path=relative,reason=str(exc)[:300]))
                continue
            total += len(text.encode('utf-8'))
            if total > 16 * 1024 * 1024:
                break
            scanned += 1
            for number, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    hits.append(dict(path=relative, start=number, end=number, text=line[:500], sha256=digest(raw),
                                     format=document['format'],locations=self.locations(document,number,number)))
                    if len(hits) >= 40:
                        return dict(hits=hits, scanned=scanned, limited=True,skipped=skipped[:20])
        return dict(hits=hits, scanned=scanned, limited=total>16*1024*1024 or self.scan_limited,skipped=skipped[:20])

    def index(self):
        chunks, count, total, skipped = [], 0, 0, []
        deadline=time.monotonic()+20
        for relative in self.files():
            if time.monotonic()>deadline:
                self.scan_limited=True;break
            try:
                raw, document = self.document(relative)
                text=document['text']
                if document.get('partial'):
                    skipped.append(dict(path=relative,reason='This document/image was only partly read. Open its preview for details.'))
            except (ValueError, OSError) as exc:
                skipped.append(dict(path=relative,reason=str(exc)[:300]))
                continue
            total += len(text.encode('utf-8'))
            if total > 8 * 1024 * 1024:
                break
            count += 1
            lines = text.splitlines()
            for i in range(0, len(lines), 40):
                part = '\n'.join(lines[i:i+40])[:12000]
                chunks.append(dict(path=relative, start=i+1, end=min(i+40,len(lines)), sha256=digest(raw), text=part,
                                   format=document['format'],locations=self.locations(document,i+1,i+40)))
                if len(chunks) >= 4000:
                    break
            if len(chunks) >= 4000:
                break
        result = dict(root=str(self.root), files=count, chunks=chunks,skipped=skipped[:20],
                      limited=total>8*1024*1024 or self.scan_limited or len(chunks)>=4000)
        atomic_write_bytes(self.cache, json.dumps(result).encode())
        return dict(files=count, chunks=len(chunks), limited=result['limited'],skipped=skipped[:20])

    def retrieve(self, query):
        if not self.folder.get('retrieval'):
            raise ValueError('Optional retrieval is off. Use direct search and reading.')
        if not isinstance(query, str) or not query.strip() or len(query)>200:
            raise ValueError('Enter a retrieval query up to 200 characters.')
        if not self.cache.exists():
            raise ValueError('Build the local index first, or use direct search and reading.')
        with self.cache.open('rb') as stream:
            raw = stream.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:
            raise ValueError('Rebuild the local index.')
        index = json.loads(raw)
        terms = set(re.findall(r'\w+', query.casefold()))
        ranked = []
        for chunk in index['chunks']:
            words = Counter(re.findall(r'\w+', chunk['text'].casefold()))
            score = sum(min(words[t], 5) for t in terms) + 3*sum(t in chunk['path'].casefold() for t in terms)
            if score:
                ranked.append((score, chunk))
        hits, stale, checked = [], False, {}
        for _, chunk in sorted(ranked, key=lambda item:-item[0]):
            name = chunk['path']
            if name not in checked:
                try:
                    checked[name] = digest(self.bytes(name))
                except (ValueError, OSError):
                    checked[name] = None
            if checked[name] != chunk['sha256']:
                stale = True
                continue
            hits.append(chunk)
            if len(hits)>=5:
                break
        return dict(hits=hits, stale=stale, limited=index.get('limited',False),skipped=index.get('skipped',[]),
                    note='Changed or removed files are omitted; rebuild to include them. Direct search reads current files.')

    def proposal(self, relative, content, base_sha256):
        if not self.folder.get('editable'):
            raise ValueError('Editing is off for this chat.')
        if not isinstance(content,str) or len(content.encode('utf-8'))>MAX_BYTES or '\x00' in content:
            raise ValueError('Propose UTF-8 text up to 256 KiB.')
        path = self.path(relative, missing=True)
        relative = self.relative(path)
        if path.suffix.casefold() in DOCUMENT_TYPES | IMAGE_TYPES | {'.doc'}:
            raise ValueError('PDF, Word and image originals are read-only here. Propose a separate .md or .txt draft instead.')
        if path.suffix.casefold() not in TEXT_TYPES or not path.parent.is_dir():
            raise ValueError('Choose a supported text file in an existing folder.')
        before, old = self.raw(relative) if path.exists() else (b'', '')
        expected = digest(before) if path.exists() else 'new'
        if base_sha256 != expected:
            raise ValueError('The file changed. Read it again before proposing an edit; use new for a new file.')
        return dict(path=relative, content=content, base_sha256=expected,
                    diff=''.join(difflib.unified_diff(old.splitlines(True),content.splitlines(True),
                                                    fromfile=relative,tofile=relative))[:80000])

    def apply(self, proposal):
        # Revalidate both permission and the exact bytes immediately before replacement.
        self.proposal(proposal['path'],proposal['content'],proposal['base_sha256'])
        path = self.path(proposal['path'], missing=True)
        atomic_write_bytes(path,proposal['content'].encode('utf-8'))
        return dict(path=proposal['path'],sha256=digest(proposal['content'].encode('utf-8')))


def tools(editable=False, retrieval=False):
    def tool(name, description, properties, required=()):
        return dict(type='function',function=dict(name=name,description=description,
                    parameters=dict(type='object',properties=properties,required=list(required),additionalProperties=False)))
    string = dict(type='string')
    result = [tool('list_files','List a folder within the user-selected workspace.',{'path':string}),
              tool('read_file','Read text/code, PDF, Word or image OCR text. Images provide recognized text only, not pixels or visual understanding. Say when OCR finds no text; never invent image contents. Cite the returned locations. Optional page selects a PDF or TIFF page. A TIFF page read returns lines local to that page; omit page for whole-document line ranges. Check unread_pages and warnings for partial OCR; read remaining pages individually. Document/image originals are read-only.',
                   {'path':string,'start':dict(type='integer'),'count':dict(type='integer'),'page':dict(type='integer')},['path']),
              tool('search_files','Search current file contents for a literal phrase without an index.',{'query':string},['query'])]
    if retrieval:
        result.append(tool('retrieve_files','Find relevant passages in the optional local keyword index. Stale files are omitted.',{'query':string},['query']))
    if editable:
        result.append(tool('propose_file_edit','Propose a complete text replacement for user review. Does NOT write the file. Read first and supply its hash; use new for a new file.',
                           {'path':string,'content':string,'base_sha256':string},['path','content','base_sha256']))
    return result

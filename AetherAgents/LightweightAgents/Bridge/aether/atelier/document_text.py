"""Read-only PDF/OOXML extraction in a time- and memory-bounded child process."""
from collections import OrderedDict
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import threading
import xml.etree.ElementTree as ET
import zipfile

DOCUMENT_TYPES = {'.pdf', '.docx', '.docm'}
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
MAX_DOCUMENT = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
_CACHE = OrderedDict()
_LOCK = threading.RLock()


def extract(raw, suffix, page=None):
    key = (hashlib.sha256(raw).hexdigest(), suffix, page)
    with _LOCK:
        if page is not None and suffix=='.pdf':
            whole=_CACHE.get((key[0],suffix,None))
            if whole and any(s.get('page')==page for s in whole['sections']):
                return whole
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    dependency = ''
    if suffix == '.pdf':
        spec = importlib.util.find_spec('pypdf')
        if not spec or not spec.origin:
            raise ValueError('PDF support is missing from this installation. Install the current Aether build.')
        dependency = str(Path(spec.origin).parent.parent)
    env = {k:v for k,v in os.environ.items() if 'API_KEY' not in k.upper() and 'TOKEN' not in k.upper()
           and not k.upper().startswith(('AETHER','PYTHON'))}
    try:
        result = subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),suffix,dependency,str(page or 0)],
            input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=55 if suffix=='.pdf' or suffix in IMAGE_TYPES else 15,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:
        raise ValueError('Document extraction reached its time limit. Try a smaller document or one PDF page.') from None
    if result.returncode or len(result.stdout)>8*1024*1024:
        raise ValueError('Document extraction exceeded its resource limit or could not finish.')
    try:
        value = json.loads(result.stdout)
    except (ValueError,UnicodeError):
        raise ValueError('Document extraction could not finish.') from None
    if value.get('error'):
        raise ValueError(value['error'])
    with _LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE)>16 or sum(len(v['text'])+len(v.get('preview','')) for v in _CACHE.values())>8*MAX_TEXT:
            _CACHE.popitem(last=False)
    return value


def limit_resources():
    limit=512*1024*1024
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                      ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',w.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                      ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
        kernel.GetCurrentProcess.restype=w.HANDLE
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        job=kernel.CreateJobObjectW(None,None)
        # Children (including the local OCR host) inherit the job. Closing its last
        # handle on worker exit also terminates descendants after a timeout.
        info=Extended();info.basic.flags=0x2300;info.process_memory=limit;info.job_memory=limit
        if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
            raise ValueError('Document reader could not establish its memory limit.')
        # The OS closes the handle when this short-lived worker exits.
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(limit,limit))


def assemble(sections, format, warnings=(), pages=0):
    lines, spans, length = [], [], 0
    for section in sections:
        text=section['text'].strip()
        if not text:
            continue
        wrapped=[]
        for line in text.splitlines():
            wrapped.extend(textwrap.wrap(line,width=400,replace_whitespace=False,drop_whitespace=False) or [''])
        start=len(lines)+1
        lines.append('['+section['label']+']');lines.extend(wrapped)
        length+=sum(len(line)+1 for line in wrapped)+len(section['label'])+3
        if length>MAX_TEXT:
            raise ValueError('Extracted text exceeds one million characters. Split this document into smaller files.')
        spans.append(dict(start=start,end=len(lines),label=section['label'],page=section.get('page')))
        if len(spans)>10000:
            raise ValueError('This document has too many text sections. Split it into smaller files.')
    if not spans:
        raise ValueError('No readable text was found. Image-only documents need OCR before Chat can read them.')
    return dict(text='\n'.join(lines),sections=spans,format=format,page_count=pages,warnings=list(warnings))


def pdf(raw, requested_page=None):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw),strict=False)
    if reader.is_encrypted:
        raise ValueError('This PDF is encrypted. Save an unlocked copy before attaching it.')
    count=len(reader.pages)
    if count>500:
        raise ValueError('PDF reading supports up to 500 pages. Split this document into smaller files.')
    if requested_page is not None and not 1<=requested_page<=count:
        raise ValueError('Choose a PDF page from this document.')
    sections,empty=[],[]
    for i,page in enumerate(reader.pages,1):
        text=page.extract_text() or ''
        if text.strip():sections.append(dict(label='PDF page '+str(i),page=i,text=text))
        else:empty.append(i)
        if sum(len(s['text']) for s in sections)>MAX_TEXT:
            raise ValueError('Extracted text exceeds one million characters. Split this PDF into smaller files.')
    warnings=['Text extraction does not describe images or guarantee the visual reading order of columns and tables.']
    if empty:
        # Standalone worker imports only its sibling helper; no user folder enters
        # sys.path. Render bytes already validated by the parent folder boundary.
        import importlib.util
        spec=importlib.util.spec_from_file_location('aether_windows_ocr',Path(__file__).with_name('windows_ocr.py'))
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        wanted=([requested_page]+[n for n in empty if n!=requested_page]) if requested_page in empty else empty
        result=helper.recognize(raw,wanted) if wanted else dict(pages=[])
        recognized=[]
        for row in result.get('pages',[]):
            number=row['page'];text=row['text'].strip()
            if text:
                sections.append(dict(label='PDF page '+str(number)+' (OCR)',page=number,text=text))
                recognized.append(number)
        if recognized:
            warnings.append('Local OCR ('+result.get('language','Windows')+') was used on pages '+', '.join(map(str,recognized))+'. Recognition can misread words, numbers and layout; check important details against the original.')
        remaining=[number for number in empty if number not in recognized]
        if result.get('error'):warnings.append(result['error'])
        if remaining:
            warnings.append('No extracted text for PDF pages '+', '.join(map(str,remaining[:30]))+('…' if len(remaining)>30 else '')+'. OCR may find no readable text or reach its limit. Select an individual page to try it directly.')
        if not sections:
            raise ValueError(result.get('error') or 'OCR found no readable text within its limits. Try an individual PDF page or a clearer scan.')
        sections.sort(key=lambda section:section['page'])
    value=assemble(sections,'pdf',warnings,count)
    value['partial']=bool(empty and remaining)
    return value


def image_document(raw, requested_page=None, *, tiff=False):
    spec=importlib.util.spec_from_file_location('aether_windows_ocr',Path(__file__).with_name('windows_ocr.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    result=helper.recognize(raw,image=True,tiff=tiff,page=requested_page)
    if result.get('error'):raise ValueError(result['error'])
    text=result.get('text','').strip()
    warnings=['Chat receives recognized text, not image pixels. OCR does not describe objects, colors, diagrams or layout and can misread text.']
    if result.get('ocr_error'):warnings.append(result['ocr_error'])
    if not tiff and result.get('frames',1)>1:warnings.append('Only the first frame of this animated image is read. Multi-page reading is available for TIFF documents.')
    if tiff:
        pages=result.get('pages',[])
        wanted=[requested_page] if requested_page else list(range(1,result['frames']+1))
        readable={row['page'] for row in pages if not row.get('error')}
        missing=[number for number in wanted if number not in readable]
        blank=[row['page'] for row in pages if not row['text'].strip() and not row.get('error')]
        if missing:
            warnings.append('TIFF pages not fully read: '+', '.join(map(str,missing))+'. Select an individual page to retry. Whole-document OCR is bounded to 25 seconds and one million text characters.')
        if blank:
            warnings.append('No readable text recognized on TIFF pages '+', '.join(map(str,blank))+'. Their scans remain available in the page selector.')
        if requested_page:
            warnings.append('Showing TIFF page '+str(requested_page)+' of '+str(result['frames'])+'. Select another page to read it.')
        sections=[dict(label='TIFF page '+str(row['page'])+' (OCR)',page=row['page'],text=row['text']) for row in pages if row['text'].strip()]
        value=assemble(sections,'image',warnings,result['frames']) if sections else dict(text='',sections=[],format='image',page_count=result['frames'],warnings=warnings)
        value.update(partial=bool(missing),blank_pages=blank,unread_pages=missing)
    elif text:
        warnings.append('Local OCR language: '+result.get('language','Windows')+'.')
        value=assemble([dict(label='Image text (OCR)',text=text)],'image',warnings)
    else:
        warnings.append('No readable text was recognized. You can still view the image preview.')
        value=dict(text='',sections=[],format='image',page_count=0,warnings=warnings)
    if not tiff:
        value['partial']=bool(result.get('ocr_error') or result.get('frames',1)>1)
    value.update(preview='data:image/png;base64,'+result['preview'],width=result['width'],height=result['height'],frames=result['frames'],preview_page=result.get('preview_page',1))
    return value


def word(raw):
    ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    def visible(node):
        if node.tag in (ns+'del',ns+'instrText',ns+'delText'):
            return ''
        if node.tag==ns+'t':return node.text or ''
        if node.tag==ns+'tab':return '\t'
        if node.tag in (ns+'br',ns+'cr'):return '\n'
        return ''.join(visible(child) for child in node)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries=archive.infolist()
        if len(entries)>10000 or sum(i.file_size for i in entries)>64*1024*1024:
            raise ValueError('The Word document expands beyond the 64 MiB reading limit.')
        names=[i.filename for i in entries]
        if len(set(names))!=len(names) or 'word/document.xml' not in names:
            raise ValueError('This is not a readable Word OOXML document.')
        selected=['word/document.xml']+sorted(n for n in names if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes)\.xml',n))
        sections=[]
        for name in selected:
            info=archive.getinfo(name)
            if info.file_size>8*1024*1024 or info.flag_bits&1:
                raise ValueError('A Word text part is encrypted or exceeds the reading limit.')
            xml=archive.read(name)
            declarations=xml.replace(b'\x00',b'').upper()
            if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                raise ValueError('Word XML with entity declarations is not supported.')
            tree=ET.fromstring(xml)
            if tree.tag.startswith('{http://purl.oclc.org/ooxml/wordprocessingml/main}'):
                # Strict OOXML uses an alternate namespace with the same text structures.
                for node in tree.iter():node.tag=node.tag.replace('{http://purl.oclc.org/ooxml/wordprocessingml/main}',ns)
            prefix='' if name=='word/document.xml' else Path(name).stem+' · '
            paragraphs,tables=0,0
            def walk(node):
                nonlocal paragraphs,tables
                if node.tag==ns+'del':return
                if node.tag==ns+'p':
                    text=visible(node)
                    if text.strip():
                        paragraphs+=1;sections.append(dict(label=prefix+'Paragraph '+str(paragraphs),text=text))
                elif node.tag==ns+'tbl':
                    tables+=1;table=tables
                    for number,row in enumerate(node.findall(ns+'tr'),1):
                        cells=[' / '.join(visible(p) for p in cell.findall('.//'+ns+'p')) for cell in row.findall(ns+'tc')]
                        sections.append(dict(label=prefix+f'Table {table} row {number}',text=' | '.join(cells)))
                else:
                    for child in node:walk(child)
            walk(tree)
        return assemble(sections,'word',['Word references use extracted paragraphs and table rows, not printed page numbers. Images, embedded objects and layout are not interpreted; macros and external links are never executed.'])


def main():
    try:
        limit_resources()
        suffix=sys.argv[1]
        if len(sys.argv)>2 and sys.argv[2]:sys.path.insert(0,sys.argv[2])
        raw=sys.stdin.buffer.read(MAX_DOCUMENT+1)
        if len(raw)>MAX_DOCUMENT:raise ValueError('Documents must be no larger than 20 MiB.')
        requested_page=int(sys.argv[3]) if len(sys.argv)>3 else 0
        value=pdf(raw,requested_page or None) if suffix=='.pdf' else word(raw) if suffix in ('.docx','.docm') else image_document(raw,requested_page or None,tiff=suffix in ('.tif','.tiff')) if suffix in IMAGE_TYPES else dict(error='Unsupported document type.')
    except ValueError as exc:
        value=dict(error=str(exc)[:500])
    except Exception:
        value=dict(error='Could not read this document. It may be damaged, encrypted or use an unsupported format.')
    sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))


if __name__=='__main__':main()

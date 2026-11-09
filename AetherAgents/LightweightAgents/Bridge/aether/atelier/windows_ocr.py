"""Local Windows PDF rendering/OCR. Document bytes travel over stdin, never a shell command."""
import base64
import json
import os
from pathlib import Path
import subprocess

# Fixed application code, executed by the system Windows PowerShell host. No user
# text, paths or PDF content are interpolated into this program. No profile loads,
# policy changes, temporary documents, network calls or external PDF relationships.
SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$watch=[Diagnostics.Stopwatch]::StartNew()
$rows=New-Object 'System.Collections.Generic.List[object]'
$failures=New-Object 'System.Collections.Generic.List[object]'
$language=''
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    $asAction=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
    function Await($operation,[Type]$resultType) {
        if($resultType){$task=$asTask.MakeGenericMethod($resultType).Invoke($null,@($operation))}
        else {$task=$asAction.Invoke($null,@($operation))}
        $remaining=[Math]::Max(1,25000-$watch.ElapsedMilliseconds)
        if(-not $task.Wait([int]$remaining)){throw 'OCR time limit reached'}
        if($resultType){return $task.Result}
    }
    $request=[Console]::In.ReadToEnd() | ConvertFrom-Json
    $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if(-not $engine){
        $english=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new('en-US')
        $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($english)
    }
    if(-not $engine -and $request.mode -ne 'image'){throw 'No OCR language installed'}
    if($engine){$language=$engine.RecognizerLanguage.LanguageTag}
    $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $writer=[Windows.Storage.Streams.DataWriter]::new($stream)
    $writer.WriteBytes([Convert]::FromBase64String($request.pdf))
    $null=Await ($writer.StoreAsync()) ([uint32])
    $null=$writer.DetachStream();$writer.Dispose();$stream.Seek(0)
    if($request.mode -eq 'image'){
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $frames=[int]$decoder.FrameCount
        if($request.tiff -and $frames -gt 500){throw 'TIFF reading supports up to 500 pages. Split this document into smaller files.'}
        $selected=if($request.page){[int]$request.page}else{1}
        if($selected -lt 1 -or $selected -gt $frames -or (-not $request.tiff -and $selected -ne 1)){throw 'Choose a TIFF page from this document.'}
        $numbers=if($request.page){@($selected)}elseif($request.tiff){@(1..$frames)}else{@(1)}
        $value=$null;$characters=0
        foreach($number in $numbers){
        if($watch.ElapsedMilliseconds -ge 24000 -or $characters -ge 800000){break}
        $bitmap=$null;$output=$null;$reader=$null
        try {
        $frame=Await ($decoder.GetFrameAsync([uint32]($number-1))) ([Windows.Graphics.Imaging.BitmapFrame])
        $width=[double]$frame.PixelWidth;$height=[double]$frame.PixelHeight
        if($width -le 0 -or $height -le 0 -or $width*$height -gt 40000000 -or [Math]::Max($width,$height) -gt 30000){throw 'Image dimensions exceed the reading limit'}
        $maximum=[Math]::Min(3200,[Windows.Media.Ocr.OcrEngine]::MaxImageDimension)
        $scale=[Math]::Min(1,$maximum/[Math]::Max($width,$height))
        $transform=[Windows.Graphics.Imaging.BitmapTransform]::new()
        $transform.ScaledWidth=[uint32][Math]::Max(1,[Math]::Round($width*$scale))
        $transform.ScaledHeight=[uint32][Math]::Max(1,[Math]::Round($height*$scale))
        $bitmap=Await ($frame.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,$transform,[Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,[Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $text='';$ocrError=''
        if($engine){
            try {
                $recognized=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
                $text=($recognized.Lines | ForEach-Object {$_.Text}) -join "`n"
                if($text.Length -gt 100000){throw 'Image text exceeds limit'}
            } catch {$text='';$ocrError='OCR could not read this image within its limits.'}
        } else {$ocrError='Install a Windows OCR language pack to recognize text in this image.'}
        $characters+=$text.Length
        $rows.Add(@{page=[int]$number;text=$text;error=$ocrError})
        if($number -eq $selected){
        $null=[Windows.Graphics.Imaging.BitmapEncoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
        $output=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
        $encoder=Await ([Windows.Graphics.Imaging.BitmapEncoder]::CreateAsync([Windows.Graphics.Imaging.BitmapEncoder]::PngEncoderId,$output)) ([Windows.Graphics.Imaging.BitmapEncoder])
        $encoder.SetSoftwareBitmap($bitmap)
        $previewScale=[Math]::Min(1,960/[Math]::Max($bitmap.PixelWidth,$bitmap.PixelHeight))
        $encoder.BitmapTransform.ScaledWidth=[uint32][Math]::Max(1,[Math]::Round($bitmap.PixelWidth*$previewScale))
        $encoder.BitmapTransform.ScaledHeight=[uint32][Math]::Max(1,[Math]::Round($bitmap.PixelHeight*$previewScale))
        Await ($encoder.FlushAsync()) $null
        if($output.Size -gt 4MB){throw 'Image preview exceeds limit'}
        $output.Seek(0)
        $reader=[Windows.Storage.Streams.DataReader]::new($output.GetInputStreamAt(0))
        $null=Await ($reader.LoadAsync([uint32]$output.Size)) ([uint32])
        $bytes=New-Object byte[] ([int]$output.Size);$reader.ReadBytes($bytes)
        $value=@{text=$text;language=$language;ocr_error=$ocrError;width=[int]$width;height=[int]$height;frames=$frames;preview_page=$selected;preview=[Convert]::ToBase64String($bytes)}
        }
        } catch {$failures.Add(@{page=[int]$number;reason='This image page could not be decoded or read within its limits.'})}
        finally {
            if($reader){$reader.Dispose()}
            if($bitmap){$bitmap.Dispose()}
            if($output){$output.Dispose()}
        }
        }
        if(-not $value){throw 'Could not decode the selected image page.'}
        $value.pages=@($rows.ToArray());$value.failures=@($failures.ToArray())
        $stream.Dispose()
        $value | ConvertTo-Json -Depth 5 -Compress
        return
    }
    $document=Await ([Windows.Data.Pdf.PdfDocument]::LoadFromStreamAsync($stream)) ([Windows.Data.Pdf.PdfDocument])
    foreach($number in $request.pages){
        if($watch.ElapsedMilliseconds -ge 25000){break}
        $page=$null;$image=$null;$bitmap=$null
        try {
            $page=$document.GetPage([uint32]($number-1))
            $width=[double]$page.Size.Width;$height=[double]$page.Size.Height
            if($width -le 0 -or $height -le 0){throw 'Invalid page dimensions'}
            $maximum=[Math]::Min(3200,[Windows.Media.Ocr.OcrEngine]::MaxImageDimension)
            $scale=[Math]::Min(2.5,$maximum/[Math]::Max($width,$height))
            $options=[Windows.Data.Pdf.PdfPageRenderOptions]::new()
            $options.DestinationWidth=[uint32][Math]::Max(1,[Math]::Round($width*$scale))
            $options.DestinationHeight=[uint32][Math]::Max(1,[Math]::Round($height*$scale))
            $image=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
            Await ($page.RenderToStreamAsync($image,$options)) $null
            $image.Seek(0)
            $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($image)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap=Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $text=($result.Lines | ForEach-Object {$_.Text}) -join "`n"
            if($text.Length -gt 100000){throw 'OCR page text limit reached'}
            $rows.Add(@{page=[int]$number;text=$text})
        } catch {$failures.Add(@{page=[int]$number;reason='OCR could not read this page within its limits.'})}
        finally {
            if($bitmap){$bitmap.Dispose()}
            if($image){$image.Dispose()}
            if($page){$page.Dispose()}
        }
    }
    $stream.Dispose()
    @{pages=@($rows.ToArray());failures=@($failures.ToArray());language=$language} | ConvertTo-Json -Depth 5 -Compress
} catch {
    $message=if($request.mode -eq 'image'){'Could not decode this image. It may be damaged, need an unavailable Windows codec, or exceed 40 megapixels / 30,000 pixels per side.'}else{'Local OCR is unavailable. Check that Windows OCR and a preferred-language OCR pack are installed.'}
    if($_.Exception.Message -like 'Choose a TIFF page*' -or $_.Exception.Message -like 'TIFF reading supports*'){$message=$_.Exception.Message}
    @{error=$message;pages=@($rows.ToArray());failures=@($failures.ToArray());language=$language} | ConvertTo-Json -Depth 5 -Compress
}
'''


def recognize(raw, pages=(), *, image=False, tiff=False, page=None):
    if os.name!='nt':
        return dict(error='Automatic OCR currently requires the Windows edition.',pages=[])
    # Get the OS directory through the OS, not PATH or a document-controlled value.
    import ctypes
    buffer=ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer,len(buffer)):
        return dict(error='Windows OCR could not be located.',pages=[])
    host=Path(buffer.value)/'WindowsPowerShell/v1.0/powershell.exe'
    request=json.dumps(dict(pdf=base64.b64encode(raw).decode('ascii'),pages=pages[:40],mode='image' if image else 'pdf',tiff=tiff,page=page)).encode('ascii')
    try:
        result=subprocess.run([str(host),'-NoLogo','-NoProfile','-NonInteractive','-Command',SCRIPT],
                              input=request,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              timeout=35,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or len(result.stdout)>8*1024*1024:
            raise ValueError('OCR process failed')
        return json.loads(result.stdout.decode('utf-8-sig'))
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return dict(error='Local OCR could not finish within its limits. Try reading one PDF page.',pages=[])

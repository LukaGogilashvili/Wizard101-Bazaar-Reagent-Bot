param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$bridge=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation' + [char]96 + '1'
} | Select-Object -First 1
function Await-Operation($Operation,[type]$ResultType) {
    $task=$bridge.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    $task.GetAwaiter().GetResult()
}
$file=Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime])
try {
    $decoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap=Await-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
    $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (!$engine) {throw 'Windows OCR language unavailable'}
    $result=Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime])
    $rows=@(foreach ($line in $result.Lines) {
        $boxes=@($line.Words | ForEach-Object { $_.BoundingRect })
        if (!$boxes.Count) {continue}
        $x=($boxes.X | Measure-Object -Minimum).Minimum
        $y=($boxes.Y | Measure-Object -Minimum).Minimum
        $right=($boxes | ForEach-Object {$_.X+$_.Width} | Measure-Object -Maximum).Maximum
        $bottom=($boxes | ForEach-Object {$_.Y+$_.Height} | Measure-Object -Maximum).Maximum
        $words=@(foreach ($word in $line.Words) {
            $box=$word.BoundingRect
            @{text=$word.Text; x=$box.X; y=$box.Y; w=$box.Width; h=$box.Height}
        })
        [pscustomobject]@{text=$line.Text; x=$x; y=$y; w=$right-$x; h=$bottom-$y; words=$words}
    })
    ConvertTo-Json -InputObject $rows -Depth 4 -Compress
} finally { $stream.Dispose() }

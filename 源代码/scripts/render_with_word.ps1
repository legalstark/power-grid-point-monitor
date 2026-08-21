param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [Parameter(Mandatory=$true)][string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    foreach ($name in @('需求文档', '设计文档', '测试文档', '佐证文档')) {
        $inputPath = Join-Path $ProjectRoot "$name.docx"
        $outputPath = Join-Path $OutputRoot "$name.pdf"
        $document = $word.Documents.Open($inputPath, $false, $true)
        try {
            # 17 = wdExportFormatPDF；0 = 全文档；0 = 打印布局。
            $document.ExportAsFixedFormat($outputPath, 17, $false, 0, 0)
        }
        finally {
            $document.Close(0)
        }
    }
}
finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null
}

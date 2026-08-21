param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$OutputRoot
)

# Word 负责将 DOCX 排版为 XPS，WPF 再把每一页无损栅格化为 PNG。
Add-Type -AssemblyName ReachFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName WindowsBase

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$xpsRoot = Join-Path $OutputRoot "xps"
New-Item -ItemType Directory -Path $xpsRoot -Force | Out-Null

Get-ChildItem -LiteralPath $ProjectRoot -Filter "*.docx" | ForEach-Object {
        # 每份文档使用独立 Word 进程，避免 Office 导出器跨文件复用时丢失 RPC 会话。
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        $baseName = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        $xpsPath = Join-Path $xpsRoot ($baseName + ".xps")
        $pageRoot = Join-Path $OutputRoot $baseName
        New-Item -ItemType Directory -Path $pageRoot -Force | Out-Null

        try {
            $document = $word.Documents.Open($_.FullName, $false, $true)
            try {
                $document.ExportAsFixedFormat($xpsPath, 18)
            }
            finally {
                if ($null -ne $document) { $document.Close($false) }
            }
        }
        finally {
            try { $word.Quit() } catch { }
        }

        $xps = New-Object System.Windows.Xps.Packaging.XpsDocument($xpsPath, [System.IO.FileAccess]::Read)
        try {
            $paginator = $xps.GetFixedDocumentSequence().DocumentPaginator
            for ($pageIndex = 0; $pageIndex -lt $paginator.PageCount; $pageIndex++) {
                $page = $paginator.GetPage($pageIndex)
                $scale = 1.5
                $pixelWidth = [int][Math]::Ceiling($page.Size.Width * $scale)
                $pixelHeight = [int][Math]::Ceiling($page.Size.Height * $scale)
                $bitmap = New-Object System.Windows.Media.Imaging.RenderTargetBitmap(
                    $pixelWidth, $pixelHeight, 144, 144,
                    [System.Windows.Media.PixelFormats]::Pbgra32
                )
                $drawing = New-Object System.Windows.Media.DrawingVisual
                $context = $drawing.RenderOpen()
                try {
                    $context.DrawRectangle(
                        [System.Windows.Media.Brushes]::White,
                        $null,
                        (New-Object System.Windows.Rect(0, 0, $page.Size.Width, $page.Size.Height))
                    )
                    $context.DrawRectangle(
                        (New-Object System.Windows.Media.VisualBrush($page.Visual)),
                        $null,
                        (New-Object System.Windows.Rect(0, 0, $page.Size.Width, $page.Size.Height))
                    )
                }
                finally {
                    $context.Close()
                }
                $bitmap.Render($drawing)
                $encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
                $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
                $pagePath = Join-Path $pageRoot ("page-{0:D2}.png" -f ($pageIndex + 1))
                $stream = [System.IO.File]::Open($pagePath, [System.IO.FileMode]::Create)
                try { $encoder.Save($stream) } finally { $stream.Dispose() }
            }
        }
        finally {
            $xps.Close()
        }
}

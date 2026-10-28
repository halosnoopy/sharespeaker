param([switch]$Preview, [string]$SnapshotFile='', [string]$CapturePath='', [switch]$CheckAsync, [int]$PreviewTab=0, [switch]$CheckUi, [int]$PreviewWidth=0, [int]$PreviewHeight=0, [switch]$SelectHardware)
if ($CheckUi) { $Preview=$true; if (-not $SnapshotFile) { $SnapshotFile=Join-Path (Split-Path $PSScriptRoot -Parent) 'tests/fixtures/preview.json' } }
if ($Preview) { [void][System.IO.Directory]::CreateDirectory((Join-Path (Split-Path $PSScriptRoot -Parent) 'dist')) }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
if ($CheckUi) { [System.Windows.Forms.Application]::SetUnhandledExceptionMode([System.Windows.Forms.UnhandledExceptionMode]::ThrowException) }
$ErrorActionPreference = 'Stop'
$script:AppRoot = $PSScriptRoot
$script:Root = Split-Path $PSScriptRoot -Parent
$script:LocalRoot = Join-Path $Root '.local'
if (-not $Preview -and -not $CheckAsync) { [void][System.IO.Directory]::CreateDirectory($LocalRoot) }
$script:SettingsPath = Join-Path $LocalRoot 'hub-settings.json'
$script:Settings = @{ Version=2; Distro='Ubuntu'; UsbId=''; BusId=''; Controller=''; InputUsbId=''; InputBusId=''; InputController=''; Source='none'; Roles=@{}; Outputs=@{} }
if (-not $Preview -and -not $CheckAsync -and (Test-Path $SettingsPath)) {
    try {
        $saved = Get-Content $SettingsPath -Raw | ConvertFrom-Json
        if ($saved.Version -eq 2) {
            foreach ($key in @('Distro','UsbId','BusId','Controller','InputUsbId','InputBusId','InputController','Source')) { if ($saved.$key) { $Settings[$key] = [string]$saved.$key } }
            foreach ($p in $saved.Roles.PSObject.Properties) { $Settings.Roles[$p.Name] = [string]$p.Value }
            foreach ($p in $saved.Outputs.PSObject.Properties) { $Settings.Outputs[$p.Name] = @{ Selected=[bool]$p.Value.Selected; Volume=[int]$p.Value.Volume; Delay=[int]$p.Value.Delay; Name=[string]$p.Value.Name } }
        } else {
            [System.Windows.Forms.MessageBox]::Show('Legacy settings are unsupported. Select your adapters and pair devices again.') | Out-Null
        }
    } catch { }
}
if ($Settings.Source -eq 'test') { $Settings.Source='none' }
$script:Pending = $null
$script:QueuedAction = $null
$script:KeepAlive = $null
$script:Snapshot = $null
$script:DeferredState = $null
$script:LastRouteVersion = 0
$script:LastRefresh = [datetime]::MinValue
$script:ExitRequested = $false
$script:Rendering = $false
$script:UiTicks = 0
$script:UiCheckSuccess = $false
$script:Discovered = $false
$script:OutputCards = @{}
$script:OutputInteraction = $false
$script:UiBusy = $false
$script:CardScale = 1.0
$script:OutputPage = 0
$script:OutputPageCount = 1
$script:ArrangingCards = $false
$script:MutationControls = New-Object System.Collections.Generic.List[System.Windows.Forms.Control]
# Reuse the existing installation lock so an older open window cannot run alongside this one.
$mutexName=if ($CheckAsync) {'Local\SnoopyHomeHubV2ReadOnlyCheck'} else {'Local\SnoopyHomeHubV2'}
$script:Mutex = New-Object System.Threading.Mutex($false, $mutexName)
if (-not $Preview -and -not $Mutex.WaitOne(0)) { [System.Windows.Forms.MessageBox]::Show('The hub is already running. Open it from the system tray.') | Out-Null; return }

function Save-Settings { if (-not $Preview -and -not $CheckAsync) { $Settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8 } }
function Quote-Argument([string]$Value) { if ($Value -and $Value -notmatch '[\s"]') { return $Value }; '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }
function Wsl-Path([string]$Path) { '/mnt/' + $Path.Substring(0,1).ToLowerInvariant() + '/' + $Path.Substring(3).Replace('\','/') }
function New-Button($Parent, [string]$Text, [scriptblock]$Handler, [bool]$Mutates=$true) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text=$Text; $b.AutoSize=$true; $b.Height=36; $b.MinimumSize=New-Object System.Drawing.Size(95,36)
    $b.FlatStyle='Flat'; $b.FlatAppearance.BorderColor=[System.Drawing.Color]::FromArgb(210,218,229)
    $b.BackColor=[System.Drawing.Color]::White; $b.Padding=New-Object System.Windows.Forms.Padding(10,2,10,2)
    $b.Margin=New-Object System.Windows.Forms.Padding(0,0,8,0)
    $b.Add_Click($Handler); $Parent.Controls.Add($b)
    if ($Mutates) { $MutationControls.Add($b) }
    return $b
}
function New-Label([string]$Text) { $label=New-Object System.Windows.Forms.Label; $label.Text=$Text; $label.AutoSize=$true; $label.Margin=New-Object System.Windows.Forms.Padding(0,8,12,8); return $label }
function New-Flow { $panel=New-Object System.Windows.Forms.FlowLayoutPanel; $panel.Dock='Fill'; $panel.WrapContents=$false; $panel.Margin=New-Object System.Windows.Forms.Padding(0); $panel.Padding=New-Object System.Windows.Forms.Padding(0,5,0,0); return $panel }
function New-Grid {
    $grid=New-Object System.Windows.Forms.DataGridView
    $grid.Dock='Fill'; $grid.BackgroundColor=[System.Drawing.Color]::White; $grid.BorderStyle='None'
    $grid.AllowUserToAddRows=$false; $grid.AllowUserToDeleteRows=$false; $grid.AllowUserToResizeRows=$false
    $grid.RowHeadersVisible=$false; $grid.MultiSelect=$false; $grid.SelectionMode='FullRowSelect'
    $grid.AutoSizeColumnsMode='Fill'; $grid.ColumnHeadersHeight=38; $grid.RowTemplate.Height=38
    $grid.EnableHeadersVisualStyles=$false; $grid.ColumnHeadersDefaultCellStyle.BackColor=[System.Drawing.Color]::FromArgb(236,241,247)
    $grid.DefaultCellStyle.SelectionBackColor=[System.Drawing.Color]::FromArgb(223,237,254)
    $grid.DefaultCellStyle.SelectionForeColor=[System.Drawing.Color]::FromArgb(20,38,60)
    $grid.GridColor=[System.Drawing.Color]::FromArgb(235,239,244)
    $grid.Add_DataError({ param($sender,$eventArgs) $eventArgs.ThrowException=$false })
    return $grid
}
function Add-Column($Grid, [string]$Name, [string]$Header, [int]$Weight, [bool]$ReadOnly=$true) {
    $col=New-Object System.Windows.Forms.DataGridViewTextBoxColumn
    $col.Name=$Name; $col.HeaderText=$Header; $col.FillWeight=$Weight; $col.ReadOnly=$ReadOnly
    [void]$Grid.Columns.Add($col)
}

$form=New-Object System.Windows.Forms.Form
$form.Text='ShareSpeaker'; $form.StartPosition='CenterScreen'
$form.Size=New-Object System.Drawing.Size(1040,760); $form.MinimumSize=New-Object System.Drawing.Size(950,710)
if ($Preview -and $PreviewWidth -gt 0 -and $PreviewHeight -gt 0) { $form.Size=New-Object System.Drawing.Size($PreviewWidth,$PreviewHeight) }
$form.Font=New-Object System.Drawing.Font('Segoe UI',10)
$form.ForeColor=[System.Drawing.Color]::FromArgb(24,40,61)
$form.AutoScaleMode='Dpi'; $form.BackColor=[System.Drawing.Color]::FromArgb(247,249,252)
$layout=New-Object System.Windows.Forms.TableLayoutPanel
$layout.Dock='Fill'; $layout.Padding=New-Object System.Windows.Forms.Padding(20); $layout.ColumnCount=1; $layout.RowCount=4
[void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',64)))
[void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',100)))
[void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',48)))
[void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',30)))
$form.Controls.Add($layout)
$header=New-Object System.Windows.Forms.TableLayoutPanel; $header.Dock='Fill'; $header.ColumnCount=2
[void]$header.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100)))
[void]$header.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('AutoSize')))
$layout.Controls.Add($header,0,0)
$headline=New-Object System.Windows.Forms.FlowLayoutPanel; $headline.Dock='Fill'; $headline.FlowDirection='TopDown'; $headline.WrapContents=$false
$title=New-Label 'ShareSpeaker'; $title.Font=New-Object System.Drawing.Font('Segoe UI',20,[System.Drawing.FontStyle]::Bold)
$title.Margin=New-Object System.Windows.Forms.Padding(0); $headline.Controls.Add($title)
$hardwareSummary=New-Label 'Choose hardware → Pair devices → Play'; $hardwareSummary.Margin=New-Object System.Windows.Forms.Padding(0); $hardwareSummary.Font=New-Object System.Drawing.Font('Segoe UI',9); $headline.Controls.Add($hardwareSummary)
$statusLabel=New-Label 'Reading device status…'; $statusLabel.Visible=$false; $header.Controls.Add($headline,0,0)
$tabs=New-Object System.Windows.Forms.TabControl; $tabs.Dock='Fill'; $layout.Controls.Add($tabs,0,1)
foreach ($tabName in @('Playback','Devices','Hardware')) { $page=New-Object System.Windows.Forms.TabPage; $page.Text=$tabName; $page.Padding=New-Object System.Windows.Forms.Padding(16); $page.BackColor=[System.Drawing.Color]::White; [void]$tabs.TabPages.Add($page) }
$tabs.TabPages[0].BackColor=[System.Drawing.Color]::FromArgb(247,249,252)
$playLayout=New-Object System.Windows.Forms.TableLayoutPanel; $playLayout.Dock='Fill'; $playLayout.ColumnCount=1; $playLayout.RowCount=5
foreach ($height in @(44,34)) { [void]$playLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',$height))) }
[void]$playLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',100)))
[void]$playLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',34)))
[void]$playLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',48)))
$tabs.TabPages[0].Controls.Add($playLayout)
$sourceFlow=New-Object System.Windows.Forms.TableLayoutPanel; $sourceFlow.Dock='Fill'; $sourceFlow.ColumnCount=2; $sourceFlow.RowCount=1; $sourceFlow.Padding=New-Object System.Windows.Forms.Padding(0,4,0,4)
[void]$sourceFlow.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',86))); [void]$sourceFlow.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100)))
$sourceCaption=New-Label 'Input'; $sourceCaption.Margin=New-Object System.Windows.Forms.Padding(0,5,0,0); $sourceCaption.ForeColor=[System.Drawing.Color]::FromArgb(87,103,124); $sourceFlow.Controls.Add($sourceCaption,0,0); $playLayout.Controls.Add($sourceFlow,0,0)
$sourceCombo=New-Object System.Windows.Forms.ComboBox; $sourceCombo.DropDownStyle='DropDownList'; $sourceCombo.DisplayMember='Label'; $sourceCombo.Dock='Fill'; $sourceCombo.Margin=New-Object System.Windows.Forms.Padding(0,1,0,0)
$sourceFlow.Controls.Add($sourceCombo,1,0); $MutationControls.Add($sourceCombo)
$sourceCombo.Add_SelectedIndexChanged({ if (-not $Rendering -and $sourceCombo.SelectedItem) { $Settings.Source=$sourceCombo.SelectedItem.Id; Save-Settings; Update-RouteLabel } })
$outputHeading=New-Object System.Windows.Forms.TableLayoutPanel; $outputHeading.Dock='Fill'; $outputHeading.ColumnCount=2
[void]$outputHeading.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100))); [void]$outputHeading.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',150)))
$outputTitle=New-Label 'Speakers'; $outputTitle.Font=New-Object System.Drawing.Font($form.Font,[System.Drawing.FontStyle]::Bold); $outputHeading.Controls.Add($outputTitle,0,0)
$outputCount=New-Label '0 selected'; $outputCount.AutoSize=$false; $outputCount.AutoEllipsis=$true; $outputCount.Margin=New-Object System.Windows.Forms.Padding(0); $outputCount.Dock='Fill'; $outputCount.TextAlign='MiddleRight'; $outputHeading.Controls.Add($outputCount,1,0); $playLayout.Controls.Add($outputHeading,0,1)
$outputPanel=New-Object System.Windows.Forms.FlowLayoutPanel; $outputPanel.Dock='Fill'; $outputPanel.AutoScroll=$false; $outputPanel.WrapContents=$true; $outputPanel.Margin=New-Object System.Windows.Forms.Padding(0); $playLayout.Controls.Add($outputPanel,0,2)
$emptyOutputs=New-Label 'No speakers yet. Pair a device or assign an output in Devices.'; $outputPanel.Controls.Add($emptyOutputs)
$routeLabel=New-Label ''; $routeLabel.Visible=$false
$pendingLabel=New-Label 'Forwarding has not started'; $pendingLabel.AutoSize=$false; $pendingLabel.Dock='Fill'; $pendingLabel.AutoEllipsis=$true; $pendingLabel.TextAlign='MiddleLeft'; $pendingLabel.Margin=New-Object System.Windows.Forms.Padding(0); $pendingLabel.ForeColor=[System.Drawing.Color]::FromArgb(87,103,124); $playLayout.Controls.Add($pendingLabel,0,3)
$playFooter=New-Object System.Windows.Forms.TableLayoutPanel; $playFooter.Dock='Fill'; $playFooter.ColumnCount=2; $playFooter.Margin=New-Object System.Windows.Forms.Padding(0)
[void]$playFooter.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100))); [void]$playFooter.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('AutoSize'))); $playLayout.Controls.Add($playFooter,0,4)
$pageFlow=New-Flow; $pageFlow.Visible=$false; $playFooter.Controls.Add($pageFlow,0,0)
$previousPage=New-Button $pageFlow 'Previous' { $script:OutputPage--; Resize-OutputCards } $false
$pageLabel=New-Label ''; $pageFlow.Controls.Add($pageLabel)
$nextPage=New-Button $pageFlow 'Next' { $script:OutputPage++; Resize-OutputCards } $false
$playActions=New-Flow; $playActions.AutoSize=$true; $playFooter.Controls.Add($playActions,1,0)
$playButton=New-Button $playActions 'Play / Apply' { Apply-Playback }
$playButton.BackColor=[System.Drawing.Color]::FromArgb(35,105,210); $playButton.ForeColor=[System.Drawing.Color]::White
$stopButton=New-Button $playActions 'Pause' { Start-Backend @{action='stop'} 'Pausing audio; keeping connections and volumes…' }
$playTools=New-Object System.Windows.Forms.ContextMenuStrip
$testButton=New-Object System.Windows.Forms.ToolStripMenuItem; $testButton.Text='Test outputs · 4 seconds'; $testButton.Add_Click({ Apply-Playback -TestTone }); [void]$playTools.Items.Add($testButton)
$recoverButton=New-Object System.Windows.Forms.ToolStripMenuItem; $recoverButton.Text='Recover phone audio'; $recoverButton.Add_Click({ Start-Backend @{action='recover-input'; source=$Settings.Source} 'Checking phone audio and recovering if needed…' 60 }); [void]$playTools.Items.Add($recoverButton)
$toolsToggle=New-Button $playActions 'More' { $playTools.Show($toolsToggle,(New-Object System.Drawing.Point(0,(-$playTools.PreferredSize.Height)))) }
$tips=New-Object System.Windows.Forms.ToolTip; $tips.SetToolTip($outputTitle,'Increase delay for the speaker that plays first. Click Apply to use your edits.')

$deviceLayout=New-Object System.Windows.Forms.TableLayoutPanel; $deviceLayout.Dock='Fill'; $deviceLayout.RowCount=4
[void]$deviceLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',100)))
[void]$deviceLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',46)))
[void]$deviceLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',46)))
[void]$deviceLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',46)))
$tabs.TabPages[1].Controls.Add($deviceLayout)
$discoverFlow=New-Flow; $deviceLayout.Controls.Add($discoverFlow,0,1)
$null=New-Button $discoverFlow 'Scan · 30 seconds' { $script:Discovered=$true; Start-Backend @{action='scan'} 'Scanning; keep your device in pairing mode…' 45 }
$null=New-Button $discoverFlow 'Stop scan' { Start-Backend @{action='scan-stop'} 'Stopping discovery…' 15 }
$null=New-Button $discoverFlow 'Pair phone · 90 seconds' { $script:Discovered=$true; Start-Backend @{action='pair-window'} 'Select the input adapter name shown in Hardware on your phone within 90 seconds…' 105 }
$deviceGrid=New-Grid; $deviceGrid.ReadOnly=$true
Add-Column $deviceGrid 'Name' 'Device' 160
Add-Column $deviceGrid 'Role' 'Role' 55
Add-Column $deviceGrid 'Hardware' 'Adapter' 85
Add-Column $deviceGrid 'Status' 'Status' 115
Add-Column $deviceGrid 'Mac' 'Bluetooth address' 125
Add-Column $deviceGrid 'Trusted' 'Trusted' 45
$deviceLayout.Controls.Add($deviceGrid,0,0)
$deviceActions=New-Flow; $deviceLayout.Controls.Add($deviceActions,0,2)
$null=New-Button $deviceActions 'Pair / Trust' { Device-Action 'pair' }
$null=New-Button $deviceActions 'Connect' { Device-Action 'connect' }
$null=New-Button $deviceActions 'Disconnect' { Device-Action 'disconnect' }
$null=New-Button $deviceActions 'Remove pairing' { Device-Action 'remove' }
$renameDeviceButton=New-Button $deviceActions 'Rename…' { Rename-Device }
$roleActions=New-Flow; $deviceLayout.Controls.Add($roleActions,0,3)
$null=New-Button $roleActions 'Set as input' { Set-DeviceRole 'input' }
$null=New-Button $roleActions 'Set as output' { Set-DeviceRole 'output' }
$roleActions.Controls.Add((New-Label 'Phones default to input; other devices to output.'))

$settingsFlow=New-Object System.Windows.Forms.FlowLayoutPanel; $settingsFlow.Dock='Fill'; $settingsFlow.FlowDirection='TopDown'; $settingsFlow.WrapContents=$false; $settingsFlow.AutoScroll=$true
$tabs.TabPages[2].Controls.Add($settingsFlow)
$settingsFlow.Controls.Add((New-Label 'Output adapter · Connect speakers'))
$adapterFlow=New-Flow; $adapterFlow.Dock='None'; $adapterFlow.Size=New-Object System.Drawing.Size(850,46); $settingsFlow.Controls.Add($adapterFlow)
$adapterCombo=New-Object System.Windows.Forms.ComboBox; $adapterCombo.DropDownStyle='DropDownList'; $adapterCombo.DisplayMember='Label'; $adapterCombo.Width=480; $adapterFlow.Controls.Add($adapterCombo); $MutationControls.Add($adapterCombo)
$null=New-Button $adapterFlow 'Detect adapters' { Refresh-Adapters }
$switchAdapterButton=New-Button $adapterFlow 'Apply hardware' { Start-Center }
$adapterStateLabel=New-Label 'Active adapter: not loaded'; $settingsFlow.Controls.Add($adapterStateLabel)
$settingsFlow.Controls.Add((New-Label 'Input adapter · Receive phone audio (can share the output adapter)'))
$inputAdapterCombo=New-Object System.Windows.Forms.ComboBox; $inputAdapterCombo.DropDownStyle='DropDownList'; $inputAdapterCombo.DisplayMember='Label'; $inputAdapterCombo.Width=760; $settingsFlow.Controls.Add($inputAdapterCombo); $MutationControls.Add($inputAdapterCombo)
$inputHardwareLabel=New-Label 'Input and output can use separate adapters. Click Apply hardware to activate.'; $settingsFlow.Controls.Add($inputHardwareLabel)
$settingsFlow.Controls.Add((New-Label 'Bluetooth names · Shown to phones during pairing'))
$nameActions=New-Flow; $nameActions.Dock='None'; $nameActions.Size=New-Object System.Drawing.Size(850,46); $settingsFlow.Controls.Add($nameActions)
$renameOutputButton=New-Button $nameActions 'Rename output adapter…' { Rename-Adapter 'output' }
$renameInputButton=New-Button $nameActions 'Rename input adapter…' { Rename-Adapter 'input' }
$inputAdapterCombo.Add_SelectedIndexChanged({ if (-not $Rendering -and $inputAdapterCombo.SelectedItem) { $inputHardwareLabel.Text='Pending input: '+$inputAdapterCombo.SelectedItem.Name+'; click Apply hardware to activate.' } })
$switchAdapterButton.Text='Apply hardware'
$adapterCombo.Add_SelectedIndexChanged({ if (-not $Rendering -and $adapterCombo.SelectedItem) { $chosen=$adapterCombo.SelectedItem; if ($Settings.BusId -ne $chosen.BusId -or $Settings.UsbId -ne $chosen.UsbId) { $adapterStateLabel.Text='Pending adapter: '+$chosen.Name+'; click Apply hardware to activate.' } } })
$settingsFlow.Controls.Add((New-Label 'WSL distribution'))
$distroBox=New-Object System.Windows.Forms.TextBox; $distroBox.Text=$Settings.Distro; $distroBox.Width=300; $settingsFlow.Controls.Add($distroBox); $MutationControls.Add($distroBox)
$settingsFlow.Controls.Add((New-Label 'Controller address (auto-detected; specify for identical adapters)'))
$controllerBox=New-Object System.Windows.Forms.TextBox; $controllerBox.Text=$Settings.Controller; $controllerBox.Width=300; $settingsFlow.Controls.Add($controllerBox); $MutationControls.Add($controllerBox)
$configActions=New-Flow; $configActions.Dock='None'; $configActions.Size=New-Object System.Drawing.Size(850,48); $settingsFlow.Controls.Add($configActions)
$null=New-Button $configActions 'Save connection' { Save-ConnectionSettings }
$null=New-Button $configActions 'Export settings' { Export-Configuration }
$null=New-Button $configActions 'Import settings' { Import-Configuration }
$null=New-Button $configActions 'Return to Windows' { Restore-Windows }
$settingsFlow.Controls.Add((New-Label 'Closing the window keeps the hub in the tray. Audio codecs are negotiated automatically.'))
$settingsFlow.Controls.Add((New-Label 'On a new computer, select adapters and pair again. Device roles, volumes and delays can be reused.'))
$diagnosticBox=New-Object System.Windows.Forms.TextBox; $diagnosticBox.Multiline=$true; $diagnosticBox.ReadOnly=$true; $diagnosticBox.ScrollBars='Vertical'; $diagnosticBox.Size=New-Object System.Drawing.Size(840,100); $settingsFlow.Controls.Add($diagnosticBox)

$bottomActions=New-Flow; $bottomActions.Padding=New-Object System.Windows.Forms.Padding(8,6,0,0); $layout.Controls.Add($bottomActions,0,2)
$startButton=New-Button $bottomActions 'Start hub' { Start-Center }
$refreshButton=New-Button $bottomActions 'Refresh' { Refresh-State }
$progressFlow=New-Flow; $layout.Controls.Add($progressFlow,0,3)
$progress=New-Object System.Windows.Forms.ProgressBar; $progress.Style='Marquee'; $progress.Width=100; $progress.Height=14; $progress.Visible=$false; $progressFlow.Controls.Add($progress)
$progressLabel=New-Label ''; $progressLabel.Margin=New-Object System.Windows.Forms.Padding(8,0,0,0); $progressFlow.Controls.Add($progressLabel)
$tray=New-Object System.Windows.Forms.NotifyIcon; $tray.Icon=[System.Drawing.SystemIcons]::Application; $tray.Text='ShareSpeaker · WSL active'; $tray.Visible=-not $Preview
$menu=New-Object System.Windows.Forms.ContextMenuStrip
[void]$menu.Items.Add('Open hub',$null,{ $form.Show(); $form.WindowState='Normal'; $form.Activate() })
[void]$menu.Items.Add('Exit hub',$null,{ $script:ExitRequested=$true; $form.Close() })
$tray.ContextMenuStrip=$menu; $tray.Add_DoubleClick({ $form.Show(); $form.WindowState='Normal'; $form.Activate() })

function Show-Status([string]$Text,[switch]$Show) {
    if (-not $Text) { return }
    $message=($Text -replace '\x1b\[[0-9;]*[A-Za-z]','').Trim()
    $statusLabel.Text=$message
    $progressLabel.Text=$message -replace '\r?\n',' '
    $tips.SetToolTip($progressLabel,$message)
    if ($Show -and -not $Preview -and -not $CheckAsync) { [void][System.Windows.Forms.MessageBox]::Show($form,$message,'ShareSpeaker','OK','Information') }
}
function Set-Busy([bool]$Busy,[string]$Label='') { $script:UiBusy=$Busy; foreach ($control in $MutationControls) { $control.Enabled=-not $Busy }; $testButton.Enabled=-not $Busy; $recoverButton.Enabled=-not $Busy; if ($Busy) { $playTools.Close() }; $progress.Visible=$Busy; if ($Busy) { $progressLabel.Text=$Label } }
function Test-PlaybackEditing {
    if ($OutputInteraction -or $sourceCombo.DroppedDown) { return $true }
    foreach ($card in $OutputCards.Values) { if ($card.VolumeNumber.ContainsFocus -or $card.DelayNumber.ContainsFocus) { return $true } }
    return $false
}
function Resize-OutputCards {
    if ($ArrangingCards -or -not $outputPanel -or $outputPanel.ClientSize.Width -le 0 -or $outputPanel.ClientSize.Height -le 0) { return }
    $script:ArrangingCards=$true; $outputPanel.SuspendLayout()
    try {
        $gap=[int](12*$CardScale); $height=[int](248*$CardScale)
        $available=$outputPanel.ClientSize.Width-2
        $columns=if ($available -ge 780*$CardScale) {2} else {1}
        $rows=[Math]::Max(1,[Math]::Floor($outputPanel.ClientSize.Height/($height+$gap)))
        $pageSize=[int]($columns*$rows)
        $script:OutputPageCount=[Math]::Max(1,[Math]::Ceiling($OutputCards.Count/$pageSize))
        $script:OutputPage=[Math]::Max(0,[Math]::Min($OutputPage,($OutputPageCount-1)))
        $width=[int]([Math]::Floor($available/$columns)-$gap)
        $index=0
        foreach ($panel in $outputPanel.Controls) {
            if (-not $panel.Tag) { continue }
            $panel.Size=New-Object System.Drawing.Size($width,$height)
            $panel.Visible=($index -ge $OutputPage*$pageSize -and $index -lt ($OutputPage+1)*$pageSize)
            $index++
        }
        $pageFlow.Visible=$OutputPageCount -gt 1
        $pageLabel.Text=($OutputPage+1).ToString()+' / '+$OutputPageCount
        $previousPage.Enabled=$OutputPage -gt 0; $nextPage.Enabled=$OutputPage -lt $OutputPageCount-1
    } finally { $outputPanel.ResumeLayout(); $script:ArrangingCards=$false }
}
function Set-OutputValue([string]$Mac,[string]$Field,[int]$Value) {
    if ($Rendering -or -not $OutputCards.ContainsKey($Mac)) { return }
    $card=$OutputCards[$Mac]
    if ($card.Syncing) { return }
    $card.Syncing=$true
    try {
        $Settings.Outputs[$Mac][$Field]=$Value
        $card[$Field+'Number'].Value=$Value
        $card[$Field+'Slider'].Value=$Value
    } finally { $card.Syncing=$false }
    # Dragging changes only the draft; playback is submitted by Apply-Playback.
    if (-not $OutputInteraction) { Save-Settings }
    Update-RouteLabel
}
function New-OutputCard([string]$Mac) {
    $panel=New-Object System.Windows.Forms.Panel; $panel.BackColor=[System.Drawing.Color]::White; $panel.Height=[int](248*$CardScale); $panel.Padding=New-Object System.Windows.Forms.Padding([int](12*$CardScale)); $panel.Margin=New-Object System.Windows.Forms.Padding(0,0,[int](12*$CardScale),[int](12*$CardScale)); $panel.Tag=$Mac
    $panel.Add_Paint({ param($sender,$e)
        $selected=$Settings.Outputs[[string]$sender.Tag].Selected
        $color=if ($selected) {[System.Drawing.Color]::FromArgb(58,119,216)} else {[System.Drawing.Color]::FromArgb(213,222,235)}
        $pen=New-Object System.Drawing.Pen($color); try { $e.Graphics.DrawRectangle($pen,0,0,($sender.Width-1),($sender.Height-1)) } finally { $pen.Dispose() }
    })
    $body=New-Object System.Windows.Forms.TableLayoutPanel; $body.Dock='Fill'; $body.ColumnCount=1; $body.RowCount=6; $body.Margin=New-Object System.Windows.Forms.Padding(0)
    foreach ($height in @(48,32,36,32,38)) { [void]$body.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',([int]($height*$CardScale))))) }
    [void]$body.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',100))); $panel.Controls.Add($body)
    $top=New-Object System.Windows.Forms.TableLayoutPanel; $top.Dock='Fill'; $top.ColumnCount=2; $top.RowCount=2; $top.Margin=New-Object System.Windows.Forms.Padding(0)
    [void]$top.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100))); [void]$top.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',([int](70*$CardScale)))))
    [void]$top.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',55))); [void]$top.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Percent',45)))
    $name=New-Label ''; $name.AutoSize=$false; $name.AutoEllipsis=$true; $name.Dock='Fill'; $name.Margin=New-Object System.Windows.Forms.Padding(0); $name.Font=New-Object System.Drawing.Font($form.Font,[System.Drawing.FontStyle]::Bold); $top.Controls.Add($name,0,0)
    $state=New-Label ''; $state.AutoSize=$false; $state.AutoEllipsis=$true; $state.Dock='Fill'; $state.Margin=New-Object System.Windows.Forms.Padding(0); $state.ForeColor=[System.Drawing.Color]::FromArgb(87,103,124); $top.Controls.Add($state,0,1)
    $selected=New-Object System.Windows.Forms.CheckBox; $selected.Text='Use'; $selected.Dock='Fill'; $selected.Tag=$Mac; $selected.Margin=New-Object System.Windows.Forms.Padding(0); $top.Controls.Add($selected,1,0); $top.SetRowSpan($selected,2); $body.Controls.Add($top,0,0)
    $card=@{Panel=$panel; Name=$name; Status=$state; Selected=$selected; Syncing=$false}
    foreach ($field in @('Volume','Delay')) {
        $isVolume=$field -eq 'Volume'; $row=if ($isVolume) {1} else {3}; $maximum=if ($isVolume) {100} else {3000}
        $heading=New-Object System.Windows.Forms.TableLayoutPanel; $heading.Dock='Fill'; $heading.ColumnCount=2; $heading.Margin=New-Object System.Windows.Forms.Padding(0)
        [void]$heading.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100))); [void]$heading.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',([int](96*$CardScale)))))
        $caption=New-Label $(if ($isVolume) {'Volume (%)'} else {'Delay (ms)'}); $caption.Margin=New-Object System.Windows.Forms.Padding(0,4,0,0); $heading.Controls.Add($caption,0,0)
        if (-not $isVolume) { $tips.SetToolTip($caption,'Target: 0–3000 ms. Increase for the speaker that plays first.') }
        $number=New-Object System.Windows.Forms.NumericUpDown; $number.Minimum=0; $number.Maximum=$maximum; $number.Increment=1; $number.Dock='Top'; $number.TextAlign='Right'; $number.Tag=@{Mac=$Mac; Field=$field}; $number.AccessibleName=$Settings.Outputs[$Mac].Name+' '+$caption.Text; $heading.Controls.Add($number,1,0); $body.Controls.Add($heading,0,$row)
        $slider=New-Object System.Windows.Forms.TrackBar; $slider.Minimum=0; $slider.Maximum=$maximum; $slider.TickStyle='None'; $slider.SmallChange=1; $slider.LargeChange=if ($isVolume) {10} else {100}; $slider.Dock='Fill'; $slider.Margin=New-Object System.Windows.Forms.Padding(0); $slider.Tag=@{Mac=$Mac; Field=$field}; $slider.AccessibleName=$number.AccessibleName
        $slider.Add_MouseDown({ $script:OutputInteraction=$true })
        $slider.Add_MouseUp({ $script:OutputInteraction=$false; Save-Settings })
        $slider.Add_MouseCaptureChanged({ param($sender,$e) if (-not $sender.Capture -and $OutputInteraction) { $script:OutputInteraction=$false; Save-Settings } })
        $slider.Add_ValueChanged({ param($sender,$e) Set-OutputValue $sender.Tag.Mac $sender.Tag.Field ([int]$sender.Value) })
        $number.Add_ValueChanged({ param($sender,$e) Set-OutputValue $sender.Tag.Mac $sender.Tag.Field ([int]$sender.Value) })
        $card[$field+'Number']=$number; $card[$field+'Slider']=$slider
        if ($isVolume) { $body.Controls.Add($slider,0,($row+1)) } else {
            $delayFlow=New-Object System.Windows.Forms.TableLayoutPanel; $delayFlow.Dock='Fill'; $delayFlow.ColumnCount=3; $delayFlow.Margin=New-Object System.Windows.Forms.Padding(0)
            [void]$delayFlow.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',([int](50*$CardScale))))); [void]$delayFlow.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Percent',100))); [void]$delayFlow.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle('Absolute',([int](50*$CardScale)))))
            foreach ($delta in @(-10,10)) {
                $step=New-Object System.Windows.Forms.Button; $step.Text=if ($delta -lt 0) {'−10'} else {'+10'}; $step.Dock='Top'; $step.Height=[int](30*$CardScale); $step.FlatStyle='Flat'; $step.FlatAppearance.BorderColor=[System.Drawing.Color]::FromArgb(213,222,235); $step.Tag=@{Mac=$Mac; Delta=$delta}; $step.AccessibleName=$Settings.Outputs[$Mac].Name+' delay '+$step.Text+' milliseconds'
                $step.Add_Click({ param($sender,$e) $mac=$sender.Tag.Mac; Set-OutputValue $mac 'Delay' ([Math]::Max(0,[Math]::Min(3000,($Settings.Outputs[$mac].Delay+$sender.Tag.Delta)))) })
                $delayFlow.Controls.Add($step,$(if ($delta -lt 0) {0} else {2}),0)
                $card[$(if ($delta -lt 0) {'Minus'} else {'Plus'})]=$step
            }
            $delayFlow.Controls.Add($slider,1,0); $body.Controls.Add($delayFlow,0,($row+1))
        }
    }
    $applied=New-Label ''; $applied.AutoSize=$false; $applied.AutoEllipsis=$true; $applied.Dock='Fill'; $applied.Margin=New-Object System.Windows.Forms.Padding(0,4,0,0); $applied.ForeColor=[System.Drawing.Color]::FromArgb(87,103,124); $body.Controls.Add($applied,0,5); $card.Applied=$applied
    $selected.Add_CheckedChanged({ param($sender,$e) if (-not $Rendering) { $Settings.Outputs[[string]$sender.Tag].Selected=$sender.Checked; $OutputCards[[string]$sender.Tag].Panel.Invalidate(); Remember-Outputs } })
    $OutputCards[$Mac]=$card; $outputPanel.Controls.Add($panel); $MutationControls.Add($panel); $panel.Enabled=-not $UiBusy
    return $card
}
function Update-OutputApplied {
    foreach ($mac in $OutputCards.Keys) {
        $live=@($Snapshot.route.outputs | Where-Object {$_.mac -eq $mac}) | Select-Object -First 1
        $draft=$Settings.Outputs[$mac]
        $OutputCards[$mac].Applied.Text=if ($live -and ($draft.Volume -ne $live.volume -or $draft.Delay -ne $live.delay -or -not $draft.Selected)) {'Applied: '+$live.volume+'%  ·  '+$live.delay+' ms'} elseif ($live -and $Snapshot.route.paused) {'Paused'} else {''}
    }
}
function Ensure-KeepAlive {
    if ($KeepAlive -and -not $KeepAlive.HasExited) { return }
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName='wsl.exe'; $psi.Arguments='-d '+(Quote-Argument $Settings.Distro)+' --exec cat'
    $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
    $script:KeepAlive=New-Object System.Diagnostics.Process; $KeepAlive.StartInfo=$psi; [void]$KeepAlive.Start()
    $script:KeepAliveOut=$KeepAlive.StandardOutput.ReadToEndAsync(); $script:KeepAliveErr=$KeepAlive.StandardError.ReadToEndAsync()
}
function Start-ProcessJob([string]$File,[string[]]$ArgumentItems,[scriptblock]$Done,[string]$Label,[int]$Timeout=45,[bool]$Passive=$false) {
    if ($Preview) { return }
    if ($Pending) { if ($Pending.Passive) { $script:QueuedAction={ Start-ProcessJob $File $ArgumentItems $Done $Label $Timeout $Passive }.GetNewClosure() }; return }
    try {
        $psi=New-Object System.Diagnostics.ProcessStartInfo; $psi.FileName=$File
        $psi.Arguments=($ArgumentItems | ForEach-Object { Quote-Argument $_ }) -join ' '
        $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
        $psi.StandardOutputEncoding=[System.Text.Encoding]::UTF8; $psi.StandardErrorEncoding=[System.Text.Encoding]::UTF8
        $p=New-Object System.Diagnostics.Process; $p.StartInfo=$psi; [void]$p.Start()
        $script:Pending=@{ Process=$p; Out=$p.StandardOutput.ReadToEndAsync(); Err=$p.StandardError.ReadToEndAsync(); Done=$Done; Deadline=[datetime]::Now.AddSeconds($Timeout); Label=$Label; Passive=$Passive }
        if (-not $Passive) { Set-Busy $true $Label }
    } catch { Show-Status $_.Exception.Message -Show; Set-Busy $false }
}
function Start-Backend([hashtable]$Payload,[string]$Label='Refresh status',[int]$Timeout=40,[scriptblock]$Then=$null) {
    if ($Preview) { $script:CapturedPayload=$Payload; return }
    if ($Pending) { if ($Pending.Passive) { $script:QueuedAction={ Start-Backend $Payload $Label $Timeout $Then }.GetNewClosure() }; return }
    Ensure-KeepAlive
    $Payload.roles=$Settings.Roles
    if (-not $Payload.ContainsKey('controller')) { $Payload.controller=$Settings.Controller }
    if (-not $Payload.ContainsKey('usbId')) { $Payload.usbId=$Settings.UsbId }
    if (-not $Payload.ContainsKey('inputController')) { $Payload.inputController=$Settings.InputController }
    if (-not $Payload.ContainsKey('inputUsbId')) { $Payload.inputUsbId=$Settings.InputUsbId }
    if ($Payload.action -eq 'status') { $Payload.discovered=$Discovered }
    $json=$Payload | ConvertTo-Json -Depth 9 -Compress
    $encoded=[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
    $callback={ param($code,$stdout,$stderr)
        try {
            if ([string]::IsNullOrWhiteSpace($stdout)) { throw ('Backend returned no status: '+$stderr) }
            $result=$stdout | ConvertFrom-Json
            if ($null -eq $result.ok) { throw 'Backend returned an unrecognized status.' }
            if ($result.message) { Show-Status $result.message }
            if (-not $result.ok) { if (-not $CheckAsync -and $Payload.action -eq 'status' -and $result.error -like '*another operation*') { return }; throw $result.error }
            if ($Payload.action -ne 'status') { $script:DeferredState=$null }
            if ($Payload.action -eq 'status' -and (Test-PlaybackEditing)) { $script:DeferredState=$result.data } else { Render-State $result.data }
            if (-not $CheckAsync -and $Payload.action -eq 'status' -and $result.data.route.needsInputLink) { Start-Backend @{action='reconcile'} 'Connecting restored phone audio…' 20 }
            if ($Then) { & $Then $result.data }
        } catch { $detail='Operation incomplete: '+$_.Exception.Message; if ($stderr) { $detail+="`r`n"+$stderr }; Show-Status $detail -Show; if ($CheckAsync) { $script:ExitRequested=$true; $form.Close() } }
    }.GetNewClosure()
    Start-ProcessJob 'wsl.exe' @('-d',$Settings.Distro,'--exec','timeout','--signal=TERM','--kill-after=3s',($Timeout.ToString()+'s'),'python3',(Wsl-Path (Join-Path $AppRoot 'hub_backend.py')),$encoded) $callback $Label ($Timeout+8) ($Payload.action -eq 'status')
}
function Refresh-State { if (-not $Pending) { Start-Backend @{action='status'} 'Reading device status…'; $script:LastRefresh=[datetime]::Now } }
function Remember-Outputs {
    if ($Rendering) { return }
    Save-Settings
    Update-RouteLabel
}
function Update-RouteLabel {
    $route=$Snapshot.route
    $playButton.Text=if ($route.paused) {'Resume / Apply'} else {'Play / Apply'}
    $selected=@($OutputCards.Keys | Where-Object {$Settings.Outputs[$_].Selected})
    $outputCount.Text='Selected: '+$selected.Count+' / '+$OutputCards.Count+' devices'
    Update-OutputApplied
    if (-not $route.target) { $routeLabel.Text='Forwarding has not started'; $pendingLabel.Text='Select an input and speakers, then click Play'; $pendingLabel.ForeColor=[System.Drawing.Color]::FromArgb(87,103,124); return }
    $names=@(foreach ($output in @($route.outputs)) { if ($output -and $output.mac) { if ($Settings.Outputs.ContainsKey($output.mac)) {$Settings.Outputs[$output.mac].Name} else {$output.mac} } }) -join ' + '
    $sourceName='No input'
    if ($route.source -eq 'local-test') { $sourceName='Local test tone (loop)' }
    if ($route.source -like 'phone:*') {
        $inputMac=$route.source.Substring(6)
        $phone=@($Snapshot.devices | Where-Object {$_.mac -eq $inputMac}) | Select-Object -First 1
        $sourceName=if ($phone) {$phone.name} else {$inputMac}
    }
    $dirty=$Settings.Source -ne $route.source -or $selected.Count -ne @($route.outputs).Count
    foreach ($mac in $selected) {
        $live=@($route.outputs | Where-Object {$_.mac -eq $mac}) | Select-Object -First 1
        if (-not $live -or $Settings.Outputs[$mac].Volume -ne $live.volume -or $Settings.Outputs[$mac].Delay -ne $live.delay) { $dirty=$true }
    }
    $prefix=if ($dirty) {'Unapplied changes · '} else {''}
    $detail=if ($route.paused) {'Paused'} elseif ($route.inputProblem) {'No phone audio · Use More → Recover phone audio'} elseif ($route.waitingInput) {'Waiting for phone audio'} elseif (-not $route.healthy) {'Route not ready'} else {'Playing'}
    $routeLabel.Text=$sourceName+' → '+$names
    $pendingLabel.Text=$prefix+$detail
    $pendingLabel.ForeColor=if ($dirty -or $route.inputProblem -or (-not $route.healthy -and -not $route.paused)) {[System.Drawing.Color]::FromArgb(151,88,0)} else {[System.Drawing.Color]::FromArgb(87,103,124)}
    if ($dirty -and -not $route.paused) { $playButton.Text='Apply changes' }
}
function Render-State($Data) {
    if (-not $Data) { return }
    $script:Snapshot=$Data; $script:Rendering=$true
    try {
        # Adopt changed live routes once; ordinary status polling must not erase edits.
        if ($Data.route.target -and $Data.route.updated -ne $LastRouteVersion) {
            $script:LastRouteVersion=$Data.route.updated
            $Settings.Source=if ($Data.route.source -eq 'test') {'none'} else {$Data.route.source}
            foreach ($mac in @($Settings.Outputs.Keys)) { $Settings.Outputs[$mac].Selected=$false }
            foreach ($output in $Data.route.outputs) {
                $device=@($Data.devices | Where-Object {$_.mac -eq $output.mac}) | Select-Object -First 1
                $name=if ($device) {$device.name} elseif ($Settings.Outputs.ContainsKey($output.mac)) {$Settings.Outputs[$output.mac].Name} else {$output.mac}
                $Settings.Outputs[$output.mac]=@{Selected=$true; Volume=[int]$output.volume; Delay=[int]$output.delay; Name=$name}
            }
            Save-Settings
        }
        $selectedMac=if ($deviceGrid.SelectedRows.Count) { [string]$deviceGrid.SelectedRows[0].Tag } else { '' }
        $deviceGrid.Rows.Clear(); $sourceCombo.Items.Clear(); $script:RenderOutputMacs=@{}
        [void]$sourceCombo.Items.Add([pscustomobject]@{Id='none'; Label='No input'})
        [void]$sourceCombo.Items.Add([pscustomobject]@{Id='local-test'; Label='Local test tone · Loop'})
        $seen=@{}
        foreach ($device in @($Data.devices | Sort-Object name)) {
            $seen[$device.mac]=$true
            $roleText=if ($device.role -eq 'input') {'Input'} else {'Output'}
            $trustedText=if ($device.trusted) {'Yes'} else {'No'}
            $hardwareText=if ($device.adapterRole -eq 'input') {'Phone input'} elseif ($device.adapterRole -eq 'output') {'Speaker output'} else {'Shared adapter'}
            $idx=$deviceGrid.Rows.Add($device.name,$roleText,$hardwareText,$device.status,$device.mac,$trustedText); $deviceGrid.Rows[$idx].Tag=$device.mac
            if ($selectedMac -eq $device.mac) { $deviceGrid.Rows[$idx].Selected=$true }
            if ($device.role -eq 'input') {
                $phoneVolume=if ($null -ne $device.phoneVolume) { ' · Phone volume '+$device.phoneVolume+'%' } else {''}
                if ($device.paired -or $Settings.Roles.ContainsKey($device.mac)) { [void]$sourceCombo.Items.Add([pscustomobject]@{Id=('phone:'+$device.mac); Label=($device.name+$phoneVolume)}) }
            } elseif ($device.paired -or $Settings.Outputs.ContainsKey($device.mac)) {
                Add-OutputCard $device.mac $device.name $device.status
            }
        }
        foreach ($mac in @($Settings.Outputs.Keys)) { if (-not $seen.ContainsKey($mac) -and $Settings.Roles[$mac] -ne 'input') { Add-OutputCard $mac $Settings.Outputs[$mac].Name 'Not paired on this adapter' } }
        foreach ($mac in @($OutputCards.Keys)) { if (-not $RenderOutputMacs.ContainsKey($mac)) { $panel=$OutputCards[$mac].Panel; [void]$MutationControls.Remove($panel); $outputPanel.Controls.Remove($panel); $panel.Dispose(); $OutputCards.Remove($mac) } }
        $emptyOutputs.Visible=$OutputCards.Count -eq 0
        Resize-OutputCards
        for ($i=0; $i -lt $sourceCombo.Items.Count; $i++) { if ($sourceCombo.Items[$i].Id -eq $Settings.Source) { $sourceCombo.SelectedIndex=$i; break } }
        if ($sourceCombo.SelectedIndex -lt 0) { [void]$sourceCombo.Items.Add([pscustomobject]@{Id=$Settings.Source; Label='Selected input unavailable · Connect or pair again'}); $sourceCombo.SelectedIndex=$sourceCombo.Items.Count-1 }
        if ($Data.controller.available) { $statusLabel.Text=if ($Data.serverReady) {'Hub ready'} else {'Click Start hub below'} } else { $statusLabel.Text='Bluetooth unavailable · Click Start hub' }
        $tips.SetToolTip($title,$statusLabel.Text)
        if ($Data.splitAdapters) {
            $hardwareSummary.Text='Phone → '+$Data.inputController.name+' → '+$Data.controller.name+' → Speakers'
            $inputHardwareLabel.Text='Phone: '+$Data.inputController.name+' ('+$Data.inputController.usbId+') → Speakers: '+$Data.controller.name+' ('+$Data.controller.usbId+')'
            $outputTitle.Text='Speakers · '+$Data.controller.name
            $sourceCaption.Text='Input'
        } else { $inputHardwareLabel.Text='Phone and speakers share the output adapter.'; $outputTitle.Text='Speakers'; $hardwareSummary.Text='Shared adapter: '+$Data.controller.name }
        if ($inputAdapterCombo.SelectedItem -and ($inputAdapterCombo.SelectedItem.UsbId -ne $Settings.InputUsbId -or $inputAdapterCombo.SelectedItem.BusId -ne $Settings.InputBusId)) { $inputHardwareLabel.Text='Pending input: '+$inputAdapterCombo.SelectedItem.Name+'; click Apply hardware to activate.' }
        if ($Data.controller.mac -and $Data.controller.usbId -eq $Settings.UsbId) { $Settings.Controller=$Data.controller.mac; $controllerBox.Text=$Data.controller.mac; Save-Settings }
        if ($adapterCombo.SelectedItem -and ($adapterCombo.SelectedItem.BusId -ne $Settings.BusId -or $adapterCombo.SelectedItem.UsbId -ne $Settings.UsbId)) {
            $adapterStateLabel.Text='Pending adapter: '+$adapterCombo.SelectedItem.Name+'; configured: '+$Settings.UsbId
        } else {
            $adapterStateLabel.Text=if ($Data.controller.available) {'Active: '+$Data.controller.name+' · '+$Data.controller.usbId+' · '+$Data.controller.mac} else {'Configured: '+$Settings.UsbId+'; not attached to WSL'}
        }
        Update-RouteLabel
        $diagnosticBox.Text='Audio service: '+$(if ($Data.serverReady) {'Dedicated PipeWire interface'} else {$Data.serverError})+"`r`n"+'Controller: '+$Data.controller.mac+"`r`n"+'Audio cards: '+$Data.cardCount+'; output endpoints: '+$Data.sinkCount
        foreach ($device in @($Data.devices)) { if ($null -ne $device.inputGain) { $diagnosticBox.AppendText("`r`n"+$device.name+' Input linear gain: '+$device.inputGain+'% (uses a different scale from phone volume)') } }
    } finally { $script:Rendering=$false }
}
function Add-OutputCard([string]$Mac,[string]$Name,[string]$State) {
    if (-not $Settings.Outputs.ContainsKey($Mac)) { $Settings.Outputs[$Mac]=@{Selected=$false; Volume=50; Delay=0; Name=$Name} }
    $item=$Settings.Outputs[$Mac]; $item.Name=$Name
    $RenderOutputMacs[$Mac]=$true
    $card=if ($OutputCards.ContainsKey($Mac)) {$OutputCards[$Mac]} else {New-OutputCard $Mac}
    $card.Name.Text=$Name; $card.Status.Text=$State; $card.Selected.Checked=[bool]$item.Selected
    $card.VolumeNumber.Value=[int]$item.Volume; $card.VolumeSlider.Value=[int]$item.Volume
    $card.DelayNumber.Value=[int]$item.Delay; $card.DelaySlider.Value=[int]$item.Delay; $card.Panel.Invalidate()
}
function Apply-Playback([switch]$TestTone) {
    if (-not $form.ValidateChildren()) { return }; Remember-Outputs
    if (-not $sourceCombo.SelectedItem) { Show-Status 'Select an input source.' -Show; return }
    $outputs=@(); foreach ($panel in $outputPanel.Controls) { $mac=[string]$panel.Tag; if ($mac -and $Settings.Outputs[$mac].Selected) { $item=$Settings.Outputs[$mac]; $outputs+=@{mac=$mac; volume=[int]$item.Volume; delay=[int]$item.Delay} } }
    if (-not $outputs.Count) { Show-Status 'Select at least one speaker.' -Show; return }
    $action=if ($TestTone) {'test'} else {'route'}
    Start-Backend @{action=$action; source=$sourceCombo.SelectedItem.Id; outputs=$outputs} $(if ($TestTone) {'Testing outputs while keeping the current input…'} else {'Preparing selected input and outputs…'}) ([Math]::Min(300,45+35*$outputs.Count))
}
function Selected-Device { if ($deviceGrid.SelectedRows.Count -eq 0) { Show-Status 'Select a device first.' -Show; return $null }; return [string]$deviceGrid.SelectedRows[0].Tag }
function Test-BluetoothName([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or [System.Text.Encoding]::UTF8.GetByteCount($Name.Trim()) -gt 248) { return $false }
    foreach ($character in $Name.ToCharArray()) { if ([char]::IsControl($character)) { return $false } }
    return $true
}
function New-NameDialog([string]$Title,[string]$CurrentName,[string]$DefaultName,[string]$Note) {
    $dialog=New-Object System.Windows.Forms.Form; $dialog.Text=$Title; $dialog.Font=$form.Font; $dialog.StartPosition='CenterParent'; $dialog.FormBorderStyle='FixedDialog'; $dialog.ClientSize=New-Object System.Drawing.Size(520,218); $dialog.MaximizeBox=$false; $dialog.MinimizeBox=$false; $dialog.ShowInTaskbar=$false
    $content=New-Object System.Windows.Forms.TableLayoutPanel; $content.Dock='Fill'; $content.Padding=New-Object System.Windows.Forms.Padding(16); $content.ColumnCount=1; $content.RowCount=4; $dialog.Controls.Add($content)
    foreach ($height in @(48,36,48,48)) { [void]$content.RowStyles.Add((New-Object System.Windows.Forms.RowStyle('Absolute',$height))) }
    $noteLabel=New-Label $Note; $noteLabel.AutoSize=$false; $noteLabel.Dock='Fill'; $noteLabel.Margin=New-Object System.Windows.Forms.Padding(0); $content.Controls.Add($noteLabel,0,0)
    $nameBox=New-Object System.Windows.Forms.TextBox; $nameBox.Text=$CurrentName; $nameBox.Dock='Top'; $nameBox.AccessibleName='Bluetooth name'; $content.Controls.Add($nameBox,0,1)
    $errorLabel=New-Label ''; $errorLabel.AutoSize=$false; $errorLabel.Dock='Fill'; $errorLabel.Margin=New-Object System.Windows.Forms.Padding(0); $errorLabel.ForeColor=[System.Drawing.Color]::FromArgb(160,40,40); $content.Controls.Add($errorLabel,0,2)
    $actions=New-Flow; $content.Controls.Add($actions,0,3)
    $save=New-Button $actions 'Save name' {} $false
    $reset=New-Button $actions 'Use default' {} $false
    $cancel=New-Button $actions 'Cancel' {} $false; $cancel.DialogResult='Cancel'
    $save.Add_Click({ if (-not (Test-BluetoothName $nameBox.Text)) { $errorLabel.Text='Enter a name without control characters (maximum 248 UTF-8 bytes).'; return }; $dialog.DialogResult='OK'; $dialog.Close() }.GetNewClosure())
    $reset.Add_Click({ $nameBox.Text=$DefaultName; $nameBox.Focus(); $nameBox.SelectAll() }.GetNewClosure())
    $dialog.AcceptButton=$save; $dialog.CancelButton=$cancel
    $dialog.Add_Shown({ $nameBox.Focus(); $nameBox.SelectAll() }.GetNewClosure())
    return @{Form=$dialog; Name=$nameBox; Save=$save; Reset=$reset; Error=$errorLabel}
}
function Show-NameDialog([string]$Title,[string]$CurrentName,[string]$DefaultName,[string]$Note) {
    $editor=New-NameDialog $Title $CurrentName $DefaultName $Note
    try { if ($editor.Form.ShowDialog($form) -eq 'OK') { return $editor.Name.Text.Trim() }; return $null } finally { $editor.Form.Dispose() }
}
function Rename-Device {
    $mac=Selected-Device; if (-not $mac) { return }
    $device=@($Snapshot.devices | Where-Object {$_.mac -eq $mac}) | Select-Object -First 1
    if (-not $device) { Show-Status 'Device is no longer available. Refresh and try again.' -Show; return }
    $default=if ($device.originalName) {$device.originalName} else {$device.name}
    $name=Show-NameDialog 'Rename device' $device.name $default 'Choose a nickname for this phone or speaker on this Bluetooth adapter.'
    if ($null -eq $name) { return }
    if (-not (Test-BluetoothName $name)) { Show-Status 'Enter a valid Bluetooth name (maximum 248 UTF-8 bytes).' -Show; return }
    if ($name -eq $device.name) { return }
    Start-Backend @{action='rename-device'; mac=$mac; name=$name; deviceDirection=$device.role} 'Saving device nickname…' 20
}
function Rename-Adapter([string]$Direction) {
    $controller=if ($Direction -eq 'input') {$Snapshot.inputController} else {$Snapshot.controller}
    if (-not $controller.available) { Show-Status 'Start the adapter before renaming it.' -Show; return }
    $outputDraft=$adapterCombo.SelectedItem; $inputDraft=$inputAdapterCombo.SelectedItem
    if (($outputDraft -and ($outputDraft.BusId -ne $Settings.BusId -or $outputDraft.UsbId -ne $Settings.UsbId)) -or ($inputDraft -and ($inputDraft.BusId -ne $Settings.InputBusId -or $inputDraft.UsbId -ne $Settings.InputUsbId))) { Show-Status 'Apply your hardware selection before renaming an adapter.' -Show; return }
    $default=if ($Direction -eq 'input' -and $Snapshot.splitAdapters) {'ShareSpeaker Input'} else {'ShareSpeaker'}
    $name=Show-NameDialog ('Rename '+$Direction+' adapter') $controller.name $default 'This is the name shown during Bluetooth pairing. Phones may cache the previous name.'
    if ($null -eq $name) { return }
    if (-not (Test-BluetoothName $name)) { Show-Status 'Enter a valid Bluetooth name (maximum 248 UTF-8 bytes).' -Show; return }
    if ($name -eq $controller.name) { return }
    Start-Backend @{action='rename-adapter'; direction=$Direction; name=$name} 'Saving Bluetooth adapter name…' 20
}
function Device-Action([string]$Action) {
    $mac=Selected-Device; if (-not $mac) { return }
    if ($Action -eq 'remove' -and [System.Windows.Forms.MessageBox]::Show('Remove the selected pairing? You will need to pair again.','Remove pairing','YesNo','Question') -ne 'Yes') { return }
    $device=@($Snapshot.devices | Where-Object {$_.mac -eq $mac}) | Select-Object -First 1
    Start-Backend @{action=$Action; mac=$mac; deviceDirection=$device.role} 'Updating selected device…' 65
}
function Set-DeviceRole([string]$Role) {
    $mac=Selected-Device; if (-not $mac) { return }; $Settings.Roles[$mac]=$Role
    if ($Role -eq 'input') { $Settings.Source='phone:'+$mac; if ($Settings.Outputs.ContainsKey($mac)) { $Settings.Outputs[$mac].Selected=$false } }
    Save-Settings; Show-Status 'Device role saved. Click Play to connect and apply.'; Refresh-State
}
function Usb-Executable { $command=Get-Command usbipd.exe -ErrorAction SilentlyContinue; if ($command) { return $command.Source }; return (Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe') }
function Parse-Adapters([string]$Text) {
    $result=@(); foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^\s*(\d+-\d+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.+?)\s{2,}(Not shared|Shared|Attached)\s*$') {
            $bus=$Matches[1]; $vidpid=$Matches[2].ToLowerInvariant(); $name=$Matches[3].Trim(); $state=$Matches[4]
            if ($name -match 'Bluetooth|\u84dd\u7259' -or $vidpid -eq $Settings.UsbId) {
                $result += [pscustomobject]@{BusId=$bus; UsbId=$vidpid; Name=$name; State=$state; Label=($name+' · '+$vidpid+' · '+$bus+' · '+$state)}
            }
        }
    }; return $result
}
function Render-Adapters($Adapters) {
    $draft=$adapterCombo.SelectedItem
    $inputDraft=$inputAdapterCombo.SelectedItem
    $script:Rendering=$true; $adapterCombo.Items.Clear()
    foreach ($adapter in $Adapters) { [void]$adapterCombo.Items.Add($adapter) }
    $wanted=if ($draft) {$draft} else {[pscustomobject]@{BusId=$Settings.BusId; UsbId=$Settings.UsbId}}
    for ($i=0; $i -lt $adapterCombo.Items.Count; $i++) { $a=$adapterCombo.Items[$i]; if ($a.UsbId -eq $wanted.UsbId -and $a.BusId -eq $wanted.BusId) { $adapterCombo.SelectedIndex=$i; break } }
    if ($adapterCombo.SelectedIndex -lt 0) {
        $same=@($Adapters | Where-Object {$_.UsbId -eq $wanted.UsbId})
        if ($same.Count -eq 1) { $adapterCombo.SelectedItem=$same[0] }
    }
    $inputAdapterCombo.Items.Clear()
    [void]$inputAdapterCombo.Items.Add([pscustomobject]@{BusId=''; UsbId=''; Name='Shared with output'; Label='Share the output adapter'})
    foreach ($adapter in $Adapters) { [void]$inputAdapterCombo.Items.Add($adapter) }
    $wantedInput=if ($inputDraft) {$inputDraft} else {[pscustomobject]@{BusId=$Settings.InputBusId; UsbId=$Settings.InputUsbId}}
    $inputAdapterCombo.SelectedIndex=0
    for ($i=1; $i -lt $inputAdapterCombo.Items.Count; $i++) { $a=$inputAdapterCombo.Items[$i]; if ($a.UsbId -eq $wantedInput.UsbId -and $a.BusId -eq $wantedInput.BusId) { $inputAdapterCombo.SelectedIndex=$i; break } }
    $script:Rendering=$false
}
function Refresh-Adapters {
    Start-ProcessJob (Usb-Executable) @('list') { param($code,$stdout,$stderr) $adapters=@(Parse-Adapters $stdout); Render-Adapters $adapters; if (-not $adapters.Count) { Show-Status ('Cannot read USB adapters: '+$stderr) -Show } } 'Reading USB adapters…'
}
function Start-Center {
    if ($Pending) { if ($Pending.Passive) { $script:QueuedAction={ Start-Center } }; return }
    $desiredOutput=$adapterCombo.SelectedItem; $desiredInput=$inputAdapterCombo.SelectedItem
    if (-not $desiredOutput) { Show-Status 'Select an output adapter.' -Show; $tabs.SelectedIndex=2; return }
    Ensure-KeepAlive
    if (-not $desiredInput -or -not $desiredInput.UsbId -or $desiredInput.BusId -eq $desiredOutput.BusId) {
        $script:InputDraft=@{BusId=''; UsbId=''; Controller=''}
        Start-OutputCenter; return
    }
    $inspectInput={param($code,$stdout,$stderr)
        $inventory=@(Parse-Adapters $stdout)
        $input=@($inventory | Where-Object {$_.BusId -eq $desiredInput.BusId -and $_.UsbId -eq $desiredInput.UsbId}) | Select-Object -First 1
        $output=@($inventory | Where-Object {$_.BusId -eq $desiredOutput.BusId -and $_.UsbId -eq $desiredOutput.UsbId}) | Select-Object -First 1
        if (-not $input -or -not $output) { Show-Status 'A selected adapter was unplugged. Detect adapters again.' -Show; return }
        foreach ($item in @($input,$output)) { if ($item.State -eq 'Not shared') { Show-Status ('Share the adapter in administrator PowerShell: usbipd bind --busid '+$item.BusId) -Show; return } }
        Set-InputDraft $input
        if ($input.State -eq 'Attached') { Start-OutputCenter } else {
            Start-ProcessJob (Usb-Executable) @('attach','--wsl','--busid',$input.BusId) {param($code,$stdout,$stderr) if ($code -ne 0) {Show-Status ('Input adapter attachment failed: '+$stdout+$stderr) -Show; return}; Start-OutputCenter} 'Attaching input adapter…' 50
        }
    }.GetNewClosure()
    Start-ProcessJob (Usb-Executable) @('list') $inspectInput 'Checking input and output adapters…'
}
function Start-OutputCenter {
    if ($Pending) { if ($Pending.Passive) { $script:QueuedAction={ Start-Center } }; return }
    $desired=$adapterCombo.SelectedItem
    if (-not $desired) { Show-Status 'Select a Bluetooth adapter.' -Show; $tabs.SelectedIndex=2; return }
    Ensure-KeepAlive
    $inspect={ param($code,$stdout,$stderr)
        $adapters=@(Parse-Adapters $stdout); Render-Adapters $adapters
        $matches=@($adapters | Where-Object { $_.UsbId -eq $desired.UsbId -and $_.BusId -eq $desired.BusId })
        if ($matches.Count -ne 1) { Show-Status 'Select a USB Bluetooth adapter in Hardware.' -Show; $tabs.SelectedIndex=2; return }
        $adapter=$matches[0]
        $old=@($adapters | Where-Object {$_.UsbId -eq $Settings.UsbId -and $_.BusId -eq $Settings.BusId}) | Select-Object -First 1
        if ($adapter.State -eq 'Not shared') { Show-Status ('Adapter is not shared. Run in administrator PowerShell: usbipd bind --busid '+$adapter.BusId+'; then start the hub.') -Show; return }
        if ($adapter.State -eq 'Attached') { Start-BlueZ $adapter $old } else {
            $attached={ param($code,$stdout,$stderr) if ($code -ne 0) { Show-Status ('Attachment failed; previous adapter selection preserved: '+$stdout+$stderr) -Show; return }; Start-BlueZ $adapter $old }.GetNewClosure()
            Start-ProcessJob (Usb-Executable) @('attach','--wsl','--busid',$adapter.BusId) $attached 'Attaching selected adapter to WSL…' 50
        }
    }.GetNewClosure()
    Start-ProcessJob (Usb-Executable) @('list') $inspect 'Checking selected Bluetooth adapter…'
}
function Start-BlueZ($Adapter,$OldAdapter) {
    $preparedInput=$script:InputDraft
    $ready={ param($code,$stdout,$stderr)
        if ($code -ne 0) { Show-Status ('Bluetooth service failed to start: '+$stdout+$stderr) -Show; return }
        $readyAdapter=$Adapter; $readyOldAdapter=$OldAdapter
        $initialized={ param($Data) Complete-AdapterSwitch $readyAdapter $readyOldAdapter $Data }.GetNewClosure()
        Start-Backend @{action='initialize'; controller=''; usbId=$Adapter.UsbId; inputController=''; inputUsbId=$preparedInput.UsbId} 'Preparing input and output adapters…' 60 $initialized
    }.GetNewClosure()
    Start-ProcessJob 'wsl.exe' @('-d',$Settings.Distro,'-u','root','--exec','sh','-c','modprobe btusb && systemctl start bluetooth') $ready 'Starting selected Bluetooth adapter…' 35
}
function Complete-AdapterSwitch($Adapter,$OldAdapter,$State) {
    $controller=$State.controller
    if (-not $controller.available -or $controller.usbId -ne $Adapter.UsbId) { Show-Status 'Selected adapter unavailable; previous selection preserved. Check firmware and connection.' -Show; return }
    $inputHardware=$script:InputDraft
    if ($inputHardware.UsbId -and (-not $State.inputController.available -or $State.inputController.usbId -ne $inputHardware.UsbId)) { Show-Status 'Input adapter unavailable; previous configuration preserved.' -Show; return }
    $inputController=$State.inputController
    $switching=$Settings.BusId -ne $Adapter.BusId -or $Settings.UsbId -ne $Adapter.UsbId -or $Settings.InputUsbId -ne $inputHardware.UsbId
    $commit={
        $Settings.BusId=$Adapter.BusId; $Settings.UsbId=$Adapter.UsbId; $Settings.Controller=$controller.mac
        $Settings.InputBusId=$inputHardware.BusId; $Settings.InputUsbId=$inputHardware.UsbId
        $Settings.InputController=if ($inputHardware.UsbId) {$inputController.mac} else {''}
        $controllerBox.Text=$controller.mac; Save-Settings
        Reset-AdapterView
        $adapterStateLabel.Text='Active: '+$Adapter.Name+' · '+$controller.mac
        Show-Status ('Now using '+$Adapter.Name+'. Volumes and delays preserved. New adapters may need new pairings.')
        $finish={
            $afterRefresh={ param($Data)
                if (@($Data.devices | Where-Object {$_.paired}).Count -eq 0) { Start-HardwarePairing }
                elseif ($Data.splitAdapters -and @($Data.devices | Where-Object {$_.paired -and $_.role -eq 'input'}).Count -eq 0) { Show-InputPairing }
            }.GetNewClosure()
            Start-Backend @{action='status'} 'Reading pairings for this adapter…' 30 $afterRefresh
        }.GetNewClosure()
        if ($switching -and $OldAdapter -and $OldAdapter.State -eq 'Attached' -and $OldAdapter.BusId -ne $Adapter.BusId -and $OldAdapter.BusId -ne $inputHardware.BusId) {
            $detached={ param($code,$stdout,$stderr) if ($code -ne 0) { Show-Status ('Previous adapter could not be returned: '+$stdout+$stderr) }; & $finish }.GetNewClosure()
            Start-ProcessJob (Usb-Executable) @('detach','--busid',$OldAdapter.BusId) $detached 'Returning previous adapter to Windows…' 30
        } else { & $finish }
    }.GetNewClosure()
    if ($switching -and $State.route.target) {
        Start-Backend @{action='release'; controller=$Settings.Controller; usbId=$Settings.UsbId} 'Releasing previous routes; preserving device settings…' 20 $commit
    } else { & $commit }
}
function Reset-AdapterView { $script:Discovered=$false; $script:DeferredState=$null; $script:LastRouteVersion=0 }
function Set-InputDraft($Adapter) { $script:InputDraft=@{BusId=$Adapter.BusId; UsbId=$Adapter.UsbId; Controller=''} }
function Show-InputPairing {
    $tabs.SelectedIndex=1
    Show-Status 'No phone paired on the input adapter. Click Pair phone, then select its name shown in Hardware on your phone.' -Show
}
function Start-HardwarePairing {
    $tabs.SelectedIndex=1; $script:Discovered=$true
    Show-Status 'No devices paired on this adapter. Scanning for speakers; use Pair phone for a phone.' -Show
    Start-Backend @{action='scan'} 'Scanning for devices on the new adapter…' 45
}
function Save-ConnectionSettings {
    if (-not $distroBox.Text.Trim()) { Show-Status 'WSL distribution cannot be empty.' -Show; return }
    if ($controllerBox.Text.Trim() -and $controllerBox.Text.Trim() -notmatch '^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$') { Show-Status 'Invalid controller address.' -Show; return }
    if ($Settings.Distro -ne $distroBox.Text.Trim() -and $KeepAlive -and -not $KeepAlive.HasExited) { $KeepAlive.StandardInput.Close(); $script:KeepAlive=$null }
    $Settings.Distro=$distroBox.Text.Trim(); $Settings.Controller=$controllerBox.Text.Trim().ToUpperInvariant(); Save-Settings; Show-Status 'Connection settings saved.'
}
function Export-Configuration {
    Remember-Outputs; $dialog=New-Object System.Windows.Forms.SaveFileDialog; $dialog.Title='Export private device settings'; $dialog.Filter='Hub configuration (*.json)|*.json'; $dialog.FileName='sharespeaker-config.json'
    if ($dialog.ShowDialog() -eq 'OK') { $Settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $dialog.FileName -Encoding UTF8; Show-Status 'Configuration exported. It contains device names and Bluetooth addresses; keep it private.' }; $dialog.Dispose()
}
function Import-Configuration {
    $dialog=New-Object System.Windows.Forms.OpenFileDialog; $dialog.Filter='Hub configuration (*.json)|*.json'
    if ($dialog.ShowDialog() -eq 'OK') {
        try {
            $config=Get-Content -LiteralPath $dialog.FileName -Raw | ConvertFrom-Json
            if ($config.Version -ne 2) { throw 'Unsupported hub configuration.' }
            $roles=@{}; $outputs=@{}
            foreach ($p in $config.Roles.PSObject.Properties) { if ($p.Name -notmatch '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' -or $p.Value -notin @('input','output')) { throw 'Invalid device role configuration.' }; $roles[$p.Name]=$p.Value }
            foreach ($p in $config.Outputs.PSObject.Properties) { if ($p.Name -notmatch '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' -or [int]$p.Value.Volume -lt 0 -or [int]$p.Value.Volume -gt 100 -or [int]$p.Value.Delay -lt 0 -or [int]$p.Value.Delay -gt 3000) { throw 'Invalid volume or delay configuration.' }; $outputs[$p.Name]=@{Selected=[bool]$p.Value.Selected; Volume=[int]$p.Value.Volume; Delay=[int]$p.Value.Delay; Name=[string]$p.Value.Name} }
            $Settings.Roles=$roles; $Settings.Outputs=$outputs; $Settings.Source=[string]$config.Source
            Save-Settings; Show-Status 'Device and routing settings imported. Local adapter settings preserved; pair again after migration.'; Refresh-State
        } catch { Show-Status ('Import failed: '+$_.Exception.Message) -Show }
    }; $dialog.Dispose()
}
function Restore-Windows {
    if (-not $Settings.BusId) { Show-Status 'No adapter selected.' -Show; return }
    if ([System.Windows.Forms.MessageBox]::Show('Stop forwarding and return the current adapter to Windows?','Return Bluetooth','YesNo','Question') -ne 'Yes') { return }
    $detach={ Start-ProcessJob (Usb-Executable) @('detach','--busid',$Settings.BusId) { param($code,$stdout,$stderr) if ($code -ne 0) { Show-Status ($stdout+$stderr) -Show } else { Show-Status 'Adapter returned to Windows.'; Refresh-State } } 'Returning adapter…' 30 }
    if ($Snapshot.serverReady) { Start-Backend @{action='release'} 'Releasing hub audio routes…' 20 $detach } else { & $detach }
}

$outputPanel.Add_SizeChanged({ Resize-OutputCards })
$timer=New-Object System.Windows.Forms.Timer; $timer.Interval=120
$timer.Add_Tick({
    $script:UiTicks++
    if ($DeferredState -and -not $Pending -and -not (Test-PlaybackEditing)) { $data=$DeferredState; $script:DeferredState=$null; Render-State $data }
    if ($Pending) {
        $job=$Pending
        if ([datetime]::Now -gt $job.Deadline -and -not $job.Process.HasExited) { try { $job.Process.Kill() } catch {}; Show-Status 'Operation timed out. Backend commands have separate time limits.' -Show }
        if ($job.Process.HasExited -and $job.Out.IsCompleted -and $job.Err.IsCompleted) {
            $script:Pending=$null
            try { & $job.Done $job.Process.ExitCode $job.Out.Result $job.Err.Result } catch { Show-Status $_.Exception.Message -Show }
            $job.Process.Dispose(); $script:LastRefresh=[datetime]::Now
            if (-not $Pending -and $QueuedAction) { $next=$QueuedAction; $script:QueuedAction=$null; & $next }
            if (-not $Pending) { Set-Busy $false }
        }
    } elseif (-not $Preview -and ([datetime]::Now-$LastRefresh).TotalSeconds -gt $(if ($tabs.SelectedIndex -eq 1 -or $Snapshot.discovering) {1} elseif ($Snapshot.route.source -like 'phone:*') {2} else {10}) -and -not (Test-PlaybackEditing)) {
        if ($KeepAlive -and $KeepAlive.HasExited) { Show-Status 'WSL keepalive exited; restarting…'; Ensure-KeepAlive }
        Refresh-State
    }
})
$form.Add_Shown({
    $graphics=$form.CreateGraphics(); try { $script:CardScale=$graphics.DpiX/96.0 } finally { $graphics.Dispose() }
    if ($Preview) {
        Set-Busy $false
        if ($SnapshotFile) {
            $previewRecord=Get-Content -LiteralPath $SnapshotFile -Raw | ConvertFrom-Json
            Render-State $previewRecord.data
            if ($previewRecord.adapters) { Render-Adapters @($previewRecord.adapters) }
        }
        if ($CheckUi) {
            . (Join-Path $Root 'tests/check_ui.ps1')
            return
        }
        $tabs.SelectedIndex=$PreviewTab
        if ($CapturePath) {
            $form.PerformLayout()
            $bitmap=New-Object System.Drawing.Bitmap($form.Width,$form.Height)
            $form.DrawToBitmap($bitmap,(New-Object System.Drawing.Rectangle(0,0,$form.Width,$form.Height)))
            $bitmap.Save($CapturePath,[System.Drawing.Imaging.ImageFormat]::Png); $bitmap.Dispose()
            $script:ExitRequested=$true; $form.Close()
        }
        return
    }
    if ($SelectHardware) { $tabs.SelectedIndex=2 }
    Ensure-KeepAlive; Show-Status 'Hub started. Closing the window keeps it in the tray with WSL active.'; $timer.Start()
    if ($CheckAsync) {
        Start-Backend @{action='status'} 'Checking backend communication…' 30 {
            if (-not $Snapshot.serverReady -or $UiTicks -lt 1 -or $KeepAlive.HasExited) { throw 'GUI async check failed.' }
            $script:UiCheckSuccess=$true
            @{ok=$true; ticks=$UiTicks; devices=@($Snapshot.devices).Count; keepalive=(-not $KeepAlive.HasExited)} | ConvertTo-Json | Set-Content (Join-Path $Root 'dist/gui-check.json') -Encoding UTF8
            $script:ExitRequested=$true; $form.Close()
        }
    } else { Refresh-Adapters; $script:QueuedAction={ Refresh-State } }
})
$form.Add_FormClosing({ param($sender,$e)
    if (-not $ExitRequested -and -not $Preview -and $e.CloseReason -eq 'UserClosing') { $e.Cancel=$true; $form.Hide(); $tray.ShowBalloonTip(1800,'ShareSpeaker','The hub is running in the tray and keeping WSL active.',[System.Windows.Forms.ToolTipIcon]::Info); return }
    if ($Pending -and -not $Preview) { $e.Cancel=$true; Show-Status 'Wait for the current operation to finish before exiting.' -Show; $script:ExitRequested=$false; $form.Show(); return }
    $timer.Stop(); $tray.Visible=$false; if ($KeepAlive -and -not $KeepAlive.HasExited) { $KeepAlive.StandardInput.Close() }
})
try { [void]$form.ShowDialog(); if (($CheckAsync -or $CheckUi) -and -not $UiCheckSuccess) { throw 'GUI check did not complete successfully.' } } finally { $tray.Dispose(); $timer.Dispose(); $playTools.Dispose(); $tips.Dispose(); $routeLabel.Dispose(); $statusLabel.Dispose(); if (-not $Preview) { try {$Mutex.ReleaseMutex()} catch {} }; $Mutex.Dispose() }

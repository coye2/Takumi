// Takumi Setup is an original launcher for the source-first build script.
// It contains no upstream AI executables or model weights.
package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"unsafe"
)

const (
	mbOK             = 0
	mbIconError      = 0x10
	mbYesNo          = 0x4
	mbIconQuestion   = 0x20
	idYes            = 6
	createNewConsole = 0x00000010
)

var (
	user32     = syscall.NewLazyDLL("user32.dll")
	messageBox = user32.NewProc("MessageBoxW")
)

func show(message, title string, flags uintptr) int {
	msg, _ := syscall.UTF16PtrFromString(message)
	caption, _ := syscall.UTF16PtrFromString(title)
	result, _, _ := messageBox.Call(0, uintptr(unsafe.Pointer(msg)), uintptr(unsafe.Pointer(caption)), flags)
	return int(result)
}
func main() {
	self, err := os.Executable()
	if err != nil {
		show("Could not find the Takumi installer directory.", "Takumi", mbOK|mbIconError)
		return
	}
	root := filepath.Dir(self)
	repair := filepath.Join(root, "REPAIR-Takumi.cmd")
	if _, err := os.Stat(repair); err != nil {
		show("The setup files are missing. Extract the entire ZIP first, then run Takumi-Setup.exe from the extracted Takumi folder.", "Takumi - Setup files missing", mbOK|mbIconError)
		return
	}
	if show("Build or repair the native Takumi app on this PC? The console will remain visible and save a diagnostic log. The installer will select Sumi 1.0 (~8.3 GB) or Rei 1.0 (~3.2 GB) according to this PC's hardware. Official external runtimes will be downloaded if missing. Nothing paid is required.", "Takumi - Native Setup", mbYesNo|mbIconQuestion) != idYes {
		return
	}
	cmd := exec.Command("cmd.exe", "/d", "/c", repair)
	cmd.Dir = root
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNewConsole}
	if err := cmd.Start(); err != nil {
		show("Could not launch Takumi Repair. Run REPAIR-Takumi.cmd directly.\n\n"+err.Error(), "Takumi - Setup failed", mbOK|mbIconError)
	}
}

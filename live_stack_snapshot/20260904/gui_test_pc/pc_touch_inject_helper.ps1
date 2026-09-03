param(
    [Parameter(Mandatory=$true)]
    [string]$ContactsJson,
    [int]$Taps = 10,
    [int]$HoldMs = 80,
    [int]$GapMs = 300,
    [int]$Radius = 8,
    [int]$Pressure = 512
)

$ErrorActionPreference = "Stop"

$source = @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;

public static class GuiTestPcTouchNative
{
    private const uint PT_TOUCH = 0x00000002;
    private const uint POINTER_FLAG_INRANGE = 0x00000002;
    private const uint POINTER_FLAG_INCONTACT = 0x00000004;
    private const uint POINTER_FLAG_PRIMARY = 0x00002000;
    private const uint POINTER_FLAG_DOWN = 0x00010000;
    private const uint POINTER_FLAG_UPDATE = 0x00020000;
    private const uint POINTER_FLAG_UP = 0x00040000;
    private const uint TOUCH_MASK_CONTACTAREA = 0x00000001;
    private const uint TOUCH_FEEDBACK_DEFAULT = 0x00000001;

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT
    {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int left;
        public int top;
        public int right;
        public int bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINTER_INFO
    {
        public uint pointerType;
        public uint pointerId;
        public uint frameId;
        public uint pointerFlags;
        public IntPtr sourceDevice;
        public IntPtr hwndTarget;
        public POINT ptPixelLocation;
        public POINT ptHimetricLocation;
        public POINT ptPixelLocationRaw;
        public POINT ptHimetricLocationRaw;
        public uint dwTime;
        public uint historyCount;
        public int InputData;
        public uint dwKeyStates;
        public ulong PerformanceCount;
        public int ButtonChangeType;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINTER_TOUCH_INFO
    {
        public POINTER_INFO pointerInfo;
        public uint touchFlags;
        public uint touchMask;
        public RECT rcContact;
        public RECT rcContactRaw;
        public uint orientation;
        public uint pressure;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct Contact
    {
        public int X;
        public int Y;
        public IntPtr Hwnd;
    }

    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool InitializeTouchInjection(uint maxCount, uint dwMode);

    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool InjectTouchInput(uint count, [In] POINTER_TOUCH_INFO[] contacts);

    public static string Layout()
    {
        return "POINTER_INFO=" + Marshal.SizeOf(typeof(POINTER_INFO)).ToString() +
            " POINTER_TOUCH_INFO=" + Marshal.SizeOf(typeof(POINTER_TOUCH_INFO)).ToString();
    }

    public static void Run(Contact[] contacts, int taps, int holdMs, int gapMs, int radius, int pressure)
    {
        if (contacts == null || contacts.Length == 0) throw new ArgumentException("contacts empty");
        if (!InitializeTouchInjection((uint)contacts.Length, TOUCH_FEEDBACK_DEFAULT))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "InitializeTouchInjection failed");
        }
        int safePressure = Math.Max(0, Math.Min(1024, pressure));
        for (int tap = 1; tap <= taps; tap++)
        {
            Inject(BuildFrame(contacts, POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN, radius, safePressure));
            if (holdMs > 0)
            {
                Thread.Sleep(holdMs);
                Inject(BuildFrame(contacts, POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_UPDATE, radius, safePressure));
                Thread.Sleep(Math.Min(30, holdMs));
            }
            Inject(BuildFrame(contacts, POINTER_FLAG_UP, radius, safePressure));
            if (tap < taps && gapMs > 0) Thread.Sleep(gapMs);
        }
    }

    public static void Diagnose(Contact[] contacts, int radius, int pressure)
    {
        int[] counts = new int[] { 1, 2, 5, 10, contacts.Length };
        foreach (int count in counts)
        {
            if (count < 1 || count > contacts.Length) continue;
            Contact[] subset = new Contact[count];
            Array.Copy(contacts, subset, count);
            try
            {
                Console.WriteLine("diagnose_down contacts=" + count);
                Inject(BuildFrame(subset, POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN, radius, pressure));
                Thread.Sleep(50);
                Inject(BuildFrame(subset, POINTER_FLAG_UP, radius, pressure));
                Console.WriteLine("diagnose_ok contacts=" + count);
            }
            catch (Exception ex)
            {
                Console.WriteLine("diagnose_error contacts=" + count + " " + ex.GetType().Name + " " + ex.Message);
            }
        }
    }

    private static void Inject(POINTER_TOUCH_INFO[] frame)
    {
        if (!InjectTouchInput((uint)frame.Length, frame))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "InjectTouchInput failed");
        }
    }

    private static POINTER_TOUCH_INFO[] BuildFrame(Contact[] contacts, uint flags, int radius, int pressure)
    {
        POINTER_TOUCH_INFO[] frame = new POINTER_TOUCH_INFO[contacts.Length];
        for (int i = 0; i < contacts.Length; i++)
        {
            uint pointerId = (uint)(i + 1);
            uint pointerFlags = flags;
            if (i == 0) pointerFlags |= POINTER_FLAG_PRIMARY;
            frame[i] = MakeTouch(pointerId, contacts[i], pointerFlags, radius, pressure);
        }
        return frame;
    }

    private static POINTER_TOUCH_INFO MakeTouch(uint pointerId, Contact contact, uint flags, int radius, int pressure)
    {
        POINTER_TOUCH_INFO info = new POINTER_TOUCH_INFO();
        info.pointerInfo.pointerType = PT_TOUCH;
        info.pointerInfo.pointerId = pointerId;
        info.pointerInfo.frameId = 0;
        info.pointerInfo.pointerFlags = flags;
        info.pointerInfo.sourceDevice = IntPtr.Zero;
        info.pointerInfo.hwndTarget = IntPtr.Zero;
        info.pointerInfo.ptPixelLocation = new POINT { x = contact.X, y = contact.Y };
        info.pointerInfo.ptHimetricLocation = new POINT { x = 0, y = 0 };
        info.pointerInfo.ptPixelLocationRaw = new POINT { x = contact.X, y = contact.Y };
        info.pointerInfo.ptHimetricLocationRaw = new POINT { x = 0, y = 0 };
        info.pointerInfo.dwTime = 0;
        info.pointerInfo.historyCount = 1;
        info.pointerInfo.InputData = 0;
        info.pointerInfo.dwKeyStates = 0;
        info.pointerInfo.PerformanceCount = 0;
        info.pointerInfo.ButtonChangeType = 0;
        info.touchFlags = 0;
        info.touchMask = TOUCH_MASK_CONTACTAREA;
        info.rcContact = new RECT { left = contact.X - radius, top = contact.Y - radius, right = contact.X + radius, bottom = contact.Y + radius };
        info.rcContactRaw = info.rcContact;
        info.orientation = 0;
        info.pressure = (uint)Math.Max(0, Math.Min(1024, pressure));
        return info;
    }
}
"@

Add-Type -TypeDefinition $source -Language CSharp

$items = Get-Content -LiteralPath $ContactsJson -Raw -Encoding UTF8 | ConvertFrom-Json
$contacts = New-Object 'GuiTestPcTouchNative+Contact[]' $items.Count
for ($i = 0; $i -lt $items.Count; $i++) {
    $contacts[$i].X = [int]$items[$i].x
    $contacts[$i].Y = [int]$items[$i].y
    $contacts[$i].Hwnd = [IntPtr]::Zero
}

Write-Host ("native_layout " + [GuiTestPcTouchNative]::Layout())
Write-Host ("native_contacts " + $contacts.Length)
try {
    [GuiTestPcTouchNative]::Run($contacts, $Taps, $HoldMs, $GapMs, $Radius, $Pressure)
    Write-Host "native_done"
} catch {
    Write-Host ("native_error " + $_.Exception.GetType().FullName + " " + $_.Exception.Message)
    [GuiTestPcTouchNative]::Diagnose($contacts, $Radius, $Pressure)
    exit 1
}

#!/usr/bin/env python3

"""
Use 'T' to trigger autofocus, 'IOKL,.'
for manual exposure/focus:
  Control:      key[dec/inc]  min..max
  exposure time:     I   O      1..33000 [us]
  sensitivity iso:   K   L    100..1600
  focus:             ,   .      0..255 [far..near]
To go back to auto controls:
  'E' - autoexposure
  'F' - autofocus (continuous)

Other controls:
'1' - AWB lock (true / false)
'2' - AE lock (true / false)
'3' - Select control: AWB mode
'4' - Select control: AE compensation
'5' - Select control: anti-banding/flicker mode
'6' - Select control: effect mode
'7' - Select control: brightness
'8' - Select control: contrast
'9' - Select control: saturation
'0' - Select control: sharpness
'[' - Select control: luma denoise
']' - Select control: chroma denoise
'a' 'd' - Increase/decrease dot projector intensity
'w' 's' - Increase/decrease flood LED intensity

For the 'Select control: ...' options, use these keys to modify the value:
  '-' or '_' to decrease
  '+' or '=' to increase
"""

import os
#os.environ["DEPTHAI_LEVEL"] = "debug"

import cv2
import argparse
import collections
import time
from itertools import cycle
from pathlib import Path
import sys
import cam_test_gui


def socket_type_pair(arg):
    socket, type = arg.split(',')
    if not (socket in ['rgb', 'left', 'right', 'camd']):
        raise ValueError("")
    if not (type in ['m', 'mono', 'c', 'color']):
        raise ValueError("")
    is_color = True if type in ['c', 'color'] else False
    return [socket, is_color]


parser = argparse.ArgumentParser()
parser.add_argument('-cams', '--cameras', type=socket_type_pair, nargs='+',
                    default=[['rgb', True], ['left', False],
                             ['right', False], ['camd', True]],
                    help="Which camera sockets to enable, and type: c[olor] / m[ono]. "
                    "E.g: -cams rgb,m right,c . Default: rgb,c left,m right,m camd,c")
parser.add_argument('-mres', '--mono-resolution', type=int, default=800, choices={480, 400, 720, 800},
                    help="Select mono camera resolution (height). Default: %(default)s")
parser.add_argument('-cres', '--color-resolution', default='1080', choices={'720', '800', '1080', '1200', '4k', '5mp', '12mp', '48mp'},
                    help="Select color camera resolution / height. Default: %(default)s")
parser.add_argument('-rot', '--rotate', const='all', choices={'all', 'rgb', 'mono'}, nargs="?",
                    help="Which cameras to rotate 180 degrees. All if not filtered")
parser.add_argument('-fps', '--fps', type=float, default=30,
                    help="FPS to set for all cameras")
parser.add_argument('-isp3afps', '--isp3afps', type=int, default=0,
                    help="3A FPS to set for all cameras")
parser.add_argument('-ds', '--isp-downscale', default=1, type=int,
                    help="Downscale the ISP output by this factor")
parser.add_argument('-rs', '--resizable-windows', action='store_true',
                    help="Make OpenCV windows resizable. Note: may introduce some artifacts")
parser.add_argument('-tun', '--camera-tuning', type=Path,
                    help="Path to custom camera tuning database")
parser.add_argument('-d', '--device', default="", type=str,
                    help="Optional MX ID of the device to connect to.")

parser.add_argument('-ctimeout', '--connection-timeout', default=30000,
                    help="Connection timeout in ms. Default: %(default)s (sets DEPTHAI_CONNECTION_TIMEOUT environment variable)")

parser.add_argument('-btimeout', '--boot-timeout', default=30000,
                    help="Boot timeout in ms. Default: %(default)s (sets DEPTHAI_BOOT_TIMEOUT environment variable)")

args = parser.parse_args()

# Set timeouts before importing depthai
os.environ["DEPTHAI_CONNECTION_TIMEOUT"] = str(args.connection_timeout)
os.environ["DEPTHAI_BOOT_TIMEOUT"] = str(args.boot_timeout)
import depthai as dai

if len(sys.argv) == 1:
    cam_test_gui.main()

cam_list = []
cam_type_color = {}
print("Enabled cameras:")
for socket, is_color in args.cameras:
    cam_list.append(socket)
    cam_type_color[socket] = is_color
    print(socket.rjust(7), ':', 'color' if is_color else 'mono')

print("DepthAI version:", dai.__version__)
print("DepthAI path:", dai.__file__)

cam_socket_opts = {
    'rgb': dai.CameraBoardSocket.RGB,   # Or CAM_A
    'left': dai.CameraBoardSocket.LEFT,  # Or CAM_B
    'right': dai.CameraBoardSocket.RIGHT,  # Or CAM_C
    'camd': dai.CameraBoardSocket.CAM_D,
}

cam_socket_to_name = {
    'RGB': 'rgb',
    'LEFT': 'left',
    'RIGHT': 'right',
    'CAM_D': 'camd',
}

rotate = {
    'rgb': args.rotate in ['all', 'rgb'],
    'left': args.rotate in ['all', 'mono'],
    'right': args.rotate in ['all', 'mono'],
    'camd': args.rotate in ['all', 'rgb'],
}

mono_res_opts = {
    400: dai.MonoCameraProperties.SensorResolution.THE_400_P,
    480: dai.MonoCameraProperties.SensorResolution.THE_480_P,
    720: dai.MonoCameraProperties.SensorResolution.THE_720_P,
    800: dai.MonoCameraProperties.SensorResolution.THE_800_P,
    1200: dai.MonoCameraProperties.SensorResolution.THE_1200_P,
}

color_res_opts = {
    '720':  dai.ColorCameraProperties.SensorResolution.THE_720_P,
    '800':  dai.ColorCameraProperties.SensorResolution.THE_800_P,
    '1080': dai.ColorCameraProperties.SensorResolution.THE_1080_P,
    '1200': dai.ColorCameraProperties.SensorResolution.THE_1200_P,
    '4k':   dai.ColorCameraProperties.SensorResolution.THE_4_K,
    '5mp': dai.ColorCameraProperties.SensorResolution.THE_5_MP,
    '12mp': dai.ColorCameraProperties.SensorResolution.THE_12_MP,
    '48mp': dai.ColorCameraProperties.SensorResolution.THE_48_MP,
}

def clamp(num, v0, v1):
    return max(v0, min(num, v1))

# Calculates FPS over a moving window, configurable
class FPS:
    def __init__(self, window_size=30):
        self.dq = collections.deque(maxlen=window_size)
        self.fps = 0

    def update(self, timestamp=None):
        if timestamp == None:
            timestamp = time.monotonic()
        count = len(self.dq)
        if count > 0:
            self.fps = count / (timestamp - self.dq[0])
        self.dq.append(timestamp)

    def get(self):
        return self.fps

# Start defining a pipeline
pipeline = dai.Pipeline()
# Uncomment to get better throughput
# pipeline.setXLinkChunkSize(0)

control = pipeline.createXLinkIn()
control.setStreamName('control')

cam = {}
xout = {}
for c in cam_list:
    xout[c] = pipeline.createXLinkOut()
    xout[c].setStreamName(c)
    if cam_type_color[c]:
        cam[c] = pipeline.createColorCamera()
        cam[c].setResolution(color_res_opts[args.color_resolution])
        cam[c].setIspScale(1, args.isp_downscale)
        # cam[c].initialControl.setManualFocus(85) # TODO
        cam[c].isp.link(xout[c].input)
    else:
        cam[c] = pipeline.createMonoCamera()
        cam[c].setResolution(mono_res_opts[args.mono_resolution])
        cam[c].out.link(xout[c].input)
    cam[c].setBoardSocket(cam_socket_opts[c])
    # Num frames to capture on trigger, with first to be discarded (due to degraded quality)
    # cam[c].initialControl.setExternalTrigger(2, 1)
    # cam[c].initialControl.setStrobeExternal(48, 1)
    # cam[c].initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)

    # cam[c].initialControl.setManualExposure(15000, 400) # exposure [us], iso
    # When set, takes effect after the first 2 frames
    # cam[c].initialControl.setManualWhiteBalance(4000)  # light temperature in K, 1000..12000
    control.out.link(cam[c].inputControl)
    if rotate[c]:
        cam[c].setImageOrientation(dai.CameraImageOrientation.ROTATE_180_DEG)
    cam[c].setFps(args.fps)
    cam[c].setIsp3aFps(args.isp3afps)

if args.camera_tuning:
    pipeline.setCameraTuningBlobPath(str(args.camera_tuning))


# Pipeline is defined, now we can connect to the device
device = dai.Device.getDeviceByMxId(args.device)
dai_device_args = [pipeline]
if device[0]:
    dai_device_args.append(device[1])
with dai.Device(*dai_device_args) as device:
    # print('Connected cameras:', [c.name for c in device.getConnectedCameras()])
    print('Connected cameras:')
    cam_name = {}
    for p in device.getConnectedCameraFeatures():
        print(
            f' -socket {p.socket.name:6}: {p.sensorName:6} {p.width:4} x {p.height:4} focus:', end='')
        print('auto ' if p.hasAutofocus else 'fixed', '- ', end='')
        print(*[type.name for type in p.supportedTypes])
        cam_name[cam_socket_to_name[p.socket.name]] = p.sensorName

    print('USB speed:', device.getUsbSpeed().name)

    print('IR drivers:', device.getIrDrivers())

    q = {}
    fps_host = {}  # FPS computed based on the time we receive frames in app
    fps_capt = {}  # FPS computed based on capture timestamps from device
    for c in cam_list:
        q[c] = device.getOutputQueue(name=c, maxSize=4, blocking=False)
        # The OpenCV window resize may produce some artifacts
        if args.resizable_windows:
            cv2.namedWindow(c, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(c, (640, 480))
        fps_host[c] = FPS()
        fps_capt[c] = FPS()

    controlQueue = device.getInputQueue('control')

    # Manual exposure/focus set step
    EXP_STEP = 500  # us
    ISO_STEP = 50
    LENS_STEP = 3
    DOT_STEP = 100
    FLOOD_STEP = 100
    DOT_MAX = 1200
    FLOOD_MAX = 1500

    # Defaults and limits for manual focus/exposure controls
    lensPos = 150
    lensMin = 0
    lensMax = 255

    expTime = 20000
    expMin = 1
    expMax = 33000

    sensIso = 800
    sensMin = 100
    sensMax = 1600

    dotIntensity = 0
    floodIntensity = 0

    awb_mode = cycle([item for name, item in vars(
        dai.CameraControl.AutoWhiteBalanceMode).items() if name.isupper()])
    anti_banding_mode = cycle([item for name, item in vars(
        dai.CameraControl.AntiBandingMode).items() if name.isupper()])
    effect_mode = cycle([item for name, item in vars(
        dai.CameraControl.EffectMode).items() if name.isupper()])

    ae_comp = 0
    ae_lock = False
    awb_lock = False
    saturation = 0
    contrast = 0
    brightness = 0
    sharpness = 0
    luma_denoise = 0
    chroma_denoise = 0
    control = 'none'

    print("Cam:", *['     ' + c.ljust(8)
          for c in cam_list], "[host | capture timestamp]")

    capture_list = []
    while True:
        for c in cam_list:
            pkt = q[c].tryGet()
            if pkt is not None:
                fps_host[c].update()
                fps_capt[c].update(pkt.getTimestamp().total_seconds())
                frame = pkt.getCvFrame()
                if c in capture_list:
                    width, height = pkt.getWidth(), pkt.getHeight()
                    capture_file_name = ('capture_' + c + '_' + cam_name[c]
                                         + '_' + str(width) + 'x' + str(height)
                                         + '_exp_' +
                                         str(int(
                                             pkt.getExposureTime().total_seconds()*1e6))
                                         + '_iso_' + str(pkt.getSensitivity())
                                         + '_lens_' +
                                         str(pkt.getLensPosition())
                                         + '_' + capture_time
                                         + '_' + str(pkt.getSequenceNum())
                                         + ".png"
                                         )
                    print("\nSaving:", capture_file_name)
                    cv2.imwrite(capture_file_name, frame)
                    capture_list.remove(c)

                cv2.imshow(c, frame)
        print("\rFPS:",
              *["{:6.2f}|{:6.2f}".format(fps_host[c].get(),
                                         fps_capt[c].get()) for c in cam_list],
              end='', flush=True)

        key = cv2.waitKey(1)
        if key == ord('q'):
            break
        elif key == ord('c'):
            capture_list = cam_list.copy()
            capture_time = time.strftime('%Y%m%d_%H%M%S')
        elif key == ord('t'):
            print("Autofocus trigger (and disable continuous)")
            ctrl = dai.CameraControl()
            ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.AUTO)
            ctrl.setAutoFocusTrigger()
            controlQueue.send(ctrl)
        elif key == ord('f'):
            print("Autofocus enable, continuous")
            ctrl = dai.CameraControl()
            ctrl.setAutoFocusMode(
                dai.CameraControl.AutoFocusMode.CONTINUOUS_VIDEO)
            controlQueue.send(ctrl)
        elif key == ord('e'):
            print("Autoexposure enable")
            ctrl = dai.CameraControl()
            ctrl.setAutoExposureEnable()
            controlQueue.send(ctrl)
        elif key in [ord(','), ord('.')]:
            if key == ord(','):
                lensPos -= LENS_STEP
            if key == ord('.'):
                lensPos += LENS_STEP
            lensPos = clamp(lensPos, lensMin, lensMax)
            print("Setting manual focus, lens position: ", lensPos)
            ctrl = dai.CameraControl()
            ctrl.setManualFocus(lensPos)
            controlQueue.send(ctrl)
        elif key in [ord('i'), ord('o'), ord('k'), ord('l')]:
            if key == ord('i'):
                expTime -= EXP_STEP
            if key == ord('o'):
                expTime += EXP_STEP
            if key == ord('k'):
                sensIso -= ISO_STEP
            if key == ord('l'):
                sensIso += ISO_STEP
            expTime = clamp(expTime, expMin, expMax)
            sensIso = clamp(sensIso, sensMin, sensMax)
            print("Setting manual exposure, time: ", expTime, "iso: ", sensIso)
            ctrl = dai.CameraControl()
            ctrl.setManualExposure(expTime, sensIso)
            controlQueue.send(ctrl)
        elif key == ord('1'):
            awb_lock = not awb_lock
            print("Auto white balance lock:", awb_lock)
            ctrl = dai.CameraControl()
            ctrl.setAutoWhiteBalanceLock(awb_lock)
            controlQueue.send(ctrl)
        elif key == ord('2'):
            ae_lock = not ae_lock
            print("Auto exposure lock:", ae_lock)
            ctrl = dai.CameraControl()
            ctrl.setAutoExposureLock(ae_lock)
            controlQueue.send(ctrl)
        elif key == ord('a'):
            dotIntensity = dotIntensity - DOT_STEP
            if dotIntensity < 0:
                dotIntensity = 0
            device.setIrLaserDotProjectorBrightness(dotIntensity)
        elif key == ord('d'):
            dotIntensity = dotIntensity + DOT_STEP
            if dotIntensity > DOT_MAX:
                dotIntensity = DOT_MAX
            device.setIrLaserDotProjectorBrightness(dotIntensity)
        elif key == ord('w'):
            floodIntensity = floodIntensity + FLOOD_STEP
            if floodIntensity > FLOOD_MAX:
                floodIntensity = FLOOD_MAX
            device.setIrFloodLightBrightness(floodIntensity)
        elif key == ord('s'):
            floodIntensity = floodIntensity - FLOOD_STEP
            if floodIntensity < 0:
                floodIntensity = 0
            device.setIrFloodLightBrightness(floodIntensity)
        elif key >= 0 and chr(key) in '34567890[]':
            if key == ord('3'):
                control = 'awb_mode'
            elif key == ord('4'):
                control = 'ae_comp'
            elif key == ord('5'):
                control = 'anti_banding_mode'
            elif key == ord('6'):
                control = 'effect_mode'
            elif key == ord('7'):
                control = 'brightness'
            elif key == ord('8'):
                control = 'contrast'
            elif key == ord('9'):
                control = 'saturation'
            elif key == ord('0'):
                control = 'sharpness'
            elif key == ord('['):
                control = 'luma_denoise'
            elif key == ord(']'):
                control = 'chroma_denoise'
            print("Selected control:", control)
        elif key in [ord('-'), ord('_'), ord('+'), ord('=')]:
            change = 0
            if key in [ord('-'), ord('_')]:
                change = -1
            if key in [ord('+'), ord('=')]:
                change = 1
            ctrl = dai.CameraControl()
            if control == 'none':
                print("Please select a control first using keys 3..9 0 [ ]")
            elif control == 'ae_comp':
                ae_comp = clamp(ae_comp + change, -9, 9)
                print("Auto exposure compensation:", ae_comp)
                ctrl.setAutoExposureCompensation(ae_comp)
            elif control == 'anti_banding_mode':
                abm = next(anti_banding_mode)
                print("Anti-banding mode:", abm)
                ctrl.setAntiBandingMode(abm)
            elif control == 'awb_mode':
                awb = next(awb_mode)
                print("Auto white balance mode:", awb)
                ctrl.setAutoWhiteBalanceMode(awb)
            elif control == 'effect_mode':
                eff = next(effect_mode)
                print("Effect mode:", eff)
                ctrl.setEffectMode(eff)
            elif control == 'brightness':
                brightness = clamp(brightness + change, -10, 10)
                print("Brightness:", brightness)
                ctrl.setBrightness(brightness)
            elif control == 'contrast':
                contrast = clamp(contrast + change, -10, 10)
                print("Contrast:", contrast)
                ctrl.setContrast(contrast)
            elif control == 'saturation':
                saturation = clamp(saturation + change, -10, 10)
                print("Saturation:", saturation)
                ctrl.setSaturation(saturation)
            elif control == 'sharpness':
                sharpness = clamp(sharpness + change, 0, 4)
                print("Sharpness:", sharpness)
                ctrl.setSharpness(sharpness)
            elif control == 'luma_denoise':
                luma_denoise = clamp(luma_denoise + change, 0, 4)
                print("Luma denoise:", luma_denoise)
                ctrl.setLumaDenoise(luma_denoise)
            elif control == 'chroma_denoise':
                chroma_denoise = clamp(chroma_denoise + change, 0, 4)
                print("Chroma denoise:", chroma_denoise)
                ctrl.setChromaDenoise(chroma_denoise)
            controlQueue.send(ctrl)

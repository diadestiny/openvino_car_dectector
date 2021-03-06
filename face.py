import sys
import numpy as np
import cv2
import time
import logging as log
from openvino.inference_engine import IENetwork, IEPlugin

cpu_extension = "./models/cpu_extension.dll"
plugin_dir = r"C:\Program Files (x86)\IntelSWTools\openvino\deployment_tools\inference_engine\bin\intel64\Release"

# face
model_xml = "./models/face-detection-adas-0001.xml"
model_bin = "./models/face-detection-adas-0001.bin"
# landmark
landmark_xml = "./models/landmarks-regression-retail-0009.xml"
landmark_bin = "./models/landmarks-regression-retail-0009.bin"

code = 0  # "./input/face.avi"
is_async_mode = True


def face_landmark_demo():
    log.basicConfig(format="[ %(levelname)s ] %(message)s", level=log.INFO, stream=sys.stdout)
    # Plugin initialization for specified device and load extensions library if specified
    log.info("Initializing plugin for {} device...".format("CPU"))
    plugin = IEPlugin(device="CPU", plugin_dirs=plugin_dir)
    plugin.add_cpu_extension(cpu_extension)

    # LUT
    lut = []
    lut.append((0, 0, 255))
    lut.append((255, 0, 0))
    lut.append((0, 255, 0))
    lut.append((0, 255, 255))
    lut.append((255, 0, 255))

    # Read IR
    log.info("Reading IR...")
    net = IENetwork(model=model_xml, weights=model_bin)
    landmark_net = IENetwork(model=landmark_xml, weights=landmark_bin)

    if plugin.device == "CPU":
        supported_layers = plugin.get_supported_layers(net)
        not_supported_layers = [l for l in net.layers.keys() if l not in supported_layers]
        if len(not_supported_layers) != 0:
            log.error("Following layers are not supported by the plugin for specified device {}:\n {}".
                      format(plugin.device, ', '.join(not_supported_layers)))
            log.error("Please try to specify cpu extensions library path in demo's command line parameters using -l "
                      "or --cpu_extension command line argument")
            sys.exit(1)
    assert len(net.inputs.keys()) == 1, "Demo supports only single input topologies"
    assert len(net.outputs) == 1, "Demo supports only single output topologies"

    # 获取输入输出
    input_blob = next(iter(net.inputs))
    out_blob = next(iter(net.outputs))

    lm_input_blob = next(iter(landmark_net.inputs))
    lm_output_blob = next(iter(landmark_net.outputs))

    log.info("Loading IR to the plugin...")
    exec_net = plugin.load(network=net, num_requests=2)
    lm_exec_net = plugin.load(network=landmark_net)
    # Read and pre-process input image
    n, c, h, w = net.inputs[input_blob].shape
    mn, mc, mh, mw = landmark_net.inputs[lm_input_blob].shape

    # 释放网络
    del net
    del landmark_net

    cap = cv2.VideoCapture(code)

    cur_request_id = 0
    next_request_id = 1

    log.info("Starting inference in async mode...")
    log.info("To switch between sync and async modes press Tab button")
    log.info("To stop the demo execution press Esc button")

    render_time = 0
    ret, frame = cap.read()

    print("To close the application, press 'CTRL+C' or any key with focus on the output window")
    while cap.isOpened():
        if is_async_mode:
            ret, next_frame = cap.read()
        else:
            ret, frame = cap.read()
        if not ret:
            break
        initial_w = cap.get(3)
        initial_h = cap.get(4)

        # 开启同步或者异步执行模式
        inf_start = time.time()
        if is_async_mode:
            in_frame = cv2.resize(next_frame, (w, h))
            in_frame = in_frame.transpose((2, 0, 1))  # Change data layout from HWC to CHW
            in_frame = in_frame.reshape((n, c, h, w))
            exec_net.start_async(request_id=next_request_id, inputs={input_blob: in_frame})
        else:
            in_frame = cv2.resize(frame, (w, h))
            in_frame = in_frame.transpose((2, 0, 1))  # Change data layout from HWC to CHW
            in_frame = in_frame.reshape((n, c, h, w))
            exec_net.start_async(request_id=cur_request_id, inputs={input_blob: in_frame})
        if exec_net.requests[cur_request_id].wait(-1) == 0:
            # 获取网络输出
            res = exec_net.requests[cur_request_id].outputs[out_blob]

            # 解析DetectionOut
            for obj in res[0][0]:
                if obj[2] > 0.5:
                    xmin = int(obj[3] * initial_w)
                    ymin = int(obj[4] * initial_h)
                    xmax = int(obj[5] * initial_w)
                    ymax = int(obj[6] * initial_h)
                    if xmin > 0 and ymin > 0 and (xmax < initial_w) and (ymax < initial_h):
                        roi = frame[ymin:ymax, xmin:xmax, :]
                        rh, rw = roi.shape[:2]
                        face_roi = cv2.resize(roi, (mw, mh))
                        face_roi = face_roi.transpose((2, 0, 1))
                        face_roi = face_roi.reshape((mn, mc, mh, mw))
                        lm_exec_net.infer(inputs={'0': face_roi})
                        landmark_res = lm_exec_net.requests[0].outputs[lm_output_blob]
                        landmark_res = np.reshape(landmark_res, (5, 2))
                        for m in range(len(landmark_res)):
                            x = landmark_res[m][0] * rw
                            y = landmark_res[m][1] * rh
                            cv2.circle(roi, (np.int32(x), np.int32(y)), 3, lut[m], 2, 8, 0)
                    cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), (0, 0, 255), 2, 8, 0)

            inf_end = time.time()
            det_time = inf_end - inf_start
            # Draw performance stats
            inf_time_message = "Inference time: {:.3f} ms, FPS:{:.3f}".format(det_time * 1000,
                                                                              1000 / (det_time * 1000 + 1))
            render_time_message = "OpenCV rendering time: {:.3f} ms".format(render_time * 1000)
            async_mode_message = "Async mode is on. Processing request {}".format(cur_request_id) if is_async_mode else \
                "Async mode is off. Processing request {}".format(cur_request_id)

            cv2.putText(frame, inf_time_message, (15, 15), cv2.FONT_HERSHEY_COMPLEX, 0.5, (200, 10, 10), 1)
            cv2.putText(frame, render_time_message, (15, 30), cv2.FONT_HERSHEY_COMPLEX, 0.5, (10, 10, 200), 1)
            cv2.putText(frame, async_mode_message, (10, int(initial_h - 20)), cv2.FONT_HERSHEY_COMPLEX, 0.5,
                        (10, 10, 200), 1)

        #
        render_start = time.time()
        cv2.imshow("face detection", frame)
        render_end = time.time()
        render_time = render_end - render_start

        if is_async_mode:
            cur_request_id, next_request_id = next_request_id, cur_request_id
            frame = next_frame

        key = cv2.waitKey(1)
        if key == 27:
            break

    # 释放资源
    cv2.destroyAllWindows()
    del exec_net
    del lm_exec_net
    del plugin


if __name__ == '__main__':
    sys.exit(face_landmark_demo() or 0)

import type { CapturedPcm } from "./onboardingClient";

export interface MicrophoneChoice {
  id: string;
  label: string;
}

export async function listMicrophones(): Promise<MicrophoneChoice[]> {
  const permission = await navigator.mediaDevices.getUserMedia({ audio: true });
  permission.getTracks().forEach((track) => track.stop());
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === "audioinput")
    .map((device, index) => ({
      id: device.deviceId,
      label: device.label || `Mikrofon ${index + 1}`,
    }));
}

export async function capturePcm(
  seconds: number,
  deviceId?: string,
): Promise<CapturedPcm> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: deviceId ? { deviceId: { exact: deviceId } } : true,
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(context.destination);
  await new Promise((resolve) => window.setTimeout(resolve, seconds * 1_000));
  processor.disconnect();
  source.disconnect();
  stream.getTracks().forEach((track) => track.stop());
  await context.close();
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const bytes = new Uint8Array(length * 2);
  const view = new DataView(bytes.buffer);
  let offset = 0;
  for (const chunk of chunks) {
    for (const value of chunk) {
      const bounded = Math.max(-1, Math.min(1, value));
      view.setInt16(
        offset,
        bounded < 0 ? bounded * 32768 : bounded * 32767,
        true,
      );
      offset += 2;
    }
  }
  let binary = "";
  for (let index = 0; index < bytes.length; index += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 32_768));
  }
  return { pcmBase64: btoa(binary), sampleRate: context.sampleRate };
}

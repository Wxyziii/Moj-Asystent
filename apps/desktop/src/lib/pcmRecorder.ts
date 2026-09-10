import type { CapturedPcm } from "./onboardingClient";

export interface MicrophoneChoice {
  id: string;
  label: string;
}

export function resampleTo16k(
  samples: Float32Array,
  sourceRate: number,
): Float32Array {
  if (sourceRate === 16_000) return samples;
  const targetLength = Math.max(
    1,
    Math.round((samples.length * 16_000) / sourceRate),
  );
  const output = new Float32Array(targetLength);
  const ratio = sourceRate / 16_000;
  for (let index = 0; index < targetLength; index += 1) {
    const position = index * ratio;
    const left = Math.min(samples.length - 1, Math.floor(position));
    const right = Math.min(samples.length - 1, left + 1);
    const fraction = position - left;
    output[index] = samples[left] + (samples[right] - samples[left]) * fraction;
  }
  return output;
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
  const samples = new Float32Array(
    chunks.reduce((total, chunk) => total + chunk.length, 0),
  );
  let sampleOffset = 0;
  for (const chunk of chunks) {
    samples.set(chunk, sampleOffset);
    sampleOffset += chunk.length;
  }
  const normalized = resampleTo16k(samples, context.sampleRate);
  const bytes = new Uint8Array(normalized.length * 2);
  const view = new DataView(bytes.buffer);
  let offset = 0;
  for (const value of normalized) {
    const bounded = Math.max(-1, Math.min(1, value));
    view.setInt16(
      offset,
      bounded < 0 ? bounded * 32768 : bounded * 32767,
      true,
    );
    offset += 2;
  }
  let binary = "";
  for (let index = 0; index < bytes.length; index += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 32_768));
  }
  return { pcmBase64: btoa(binary), sampleRate: 16_000 };
}

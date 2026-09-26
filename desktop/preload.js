// The only bridge between the web UI and the desktop. Keep it small.
const { contextBridge, ipcRenderer } = require("electron");

function on(channel) {
  return (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on(channel, handler);
    return () => ipcRenderer.removeListener(channel, handler);
  };
}

contextBridge.exposeInMainWorld("thinkaloud", {
  devices: () => ipcRenderer.invoke("devices"),
  startMeter: (device) => ipcRenderer.invoke("meter:start", device),
  stopMeter: () => ipcRenderer.invoke("meter:stop"),
  startRecording: (opts) => ipcRenderer.invoke("record:start", opts),
  stopRecording: () => ipcRenderer.invoke("record:stop"),
  openSessionsFolder: () => ipcRenderer.invoke("open:sessions"),
  info: () => ipcRenderer.invoke("info"),
  onMeter: on("meter"),
  onRecorder: on("recorder"),
  onProcess: on("process"),
});

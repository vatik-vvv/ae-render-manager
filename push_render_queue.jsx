/**
 * AE Render Manager — push render queue from the open After Effects session.
 * File → Scripts → Push to Render Manager (install via AERenderManager deploy).
 * Requires a saved .aep; writes scan_result.json + scan_push.json to LocalAppData.
 */
(function () {
  $.global.AERM_MODE = "push";

  function workDir() {
    try {
      var base =
        (Folder.localAppData && Folder.localAppData.fsName
          ? Folder.localAppData.fsName
          : "") + "/AERenderManager";
      var folder = new Folder(base);
      if (!folder.exists) {
        folder.create();
      }
      return base;
    } catch (e) {
      throw new Error("Cannot resolve AERenderManager work folder");
    }
  }

  function resolveScanJsx() {
    var self = new File($.fileName);
    var sibling = new File(self.parent.fsName + "/scan_render_queue.jsx");
    if (sibling.exists) {
      return sibling;
    }
    var stable = new File(workDir() + "/scan_render_queue.jsx");
    if (stable.exists) {
      return stable;
    }
    throw new Error(
      "scan_render_queue.jsx not found next to this script or in " + workDir()
    );
  }

  try {
    $.evalFile(resolveScanJsx());
  } catch (err) {
    try {
      alert("Push to Render Manager failed:\n" + String(err));
    } catch (eAlert) {}
  }
})();

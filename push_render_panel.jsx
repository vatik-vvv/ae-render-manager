/**
 * AE Render Manager — dockable ScriptUI panel.
 * After Effects: Window → AE Render Manager
 */
(function (thisObj) {
  function getLocalAppDataPath() {
    try {
      if (Folder.localAppData && Folder.localAppData.fsName) {
        return Folder.localAppData.fsName;
      }
    } catch (e0) {}
    try {
      if (Folder.userData && Folder.userData.parent) {
        return Folder.userData.parent.fsName + "/Local";
      }
    } catch (e1) {}
    throw new Error("Cannot resolve Local AppData path");
  }

  function workDir() {
    var base = getLocalAppDataPath() + "/AERenderManager";
    var folder = new Folder(base);
    if (!folder.exists) {
      folder.create();
    }
    return base;
  }

  function resolvePushJsx() {
    var self = new File($.fileName);
    var sibling = new File(self.parent.fsName + "/push_render_queue.jsx");
    if (sibling.exists) {
      return sibling;
    }
    var stable = new File(workDir() + "/push_render_queue.jsx");
    if (stable.exists) {
      return stable;
    }
    throw new Error(
      "push_render_queue.jsx not found.\n" +
        "Start AE Render Manager once (deploys scripts), or copy scripts to:\n" +
        workDir()
    );
  }

  function runPush(statusText) {
    try {
      if (statusText) {
        statusText.text = "Sending…";
      }
      $.evalFile(resolvePushJsx());
      if (statusText) {
        statusText.text = "Sent — launching or raising AE Render Manager.";
      }
    } catch (err) {
      var msg = String(err);
      if (statusText) {
        statusText.text = msg;
      }
      alert("Send to AE Render Manager failed:\n" + msg);
    }
  }

  function buildUI(thisObj) {
    var pal =
      thisObj instanceof Panel
        ? thisObj
        : new Window("palette", "AE Render Manager", undefined, {
            resizeable: true,
          });

    pal.orientation = "column";
    pal.alignChildren = ["fill", "top"];
    pal.spacing = 8;
    pal.margins = 12;

    var hint = pal.add(
      "statictext",
      undefined,
      "Save the project, then send. Auto-launch needs AE scripting: Allow Scripts to Write Files and Access Network."
    );
    hint.alignment = ["fill", "top"];

    var btn = pal.add("button", undefined, "Send to AE Render Manager");
    btn.alignment = ["fill", "top"];
    btn.preferredSize = [200, 32];

    var status = pal.add("statictext", undefined, "", { multiline: true });
    status.alignment = ["fill", "fill"];

    btn.onClick = function () {
      runPush(status);
    };

    pal.onResizing = pal.onResize = function () {
      this.layout.resize();
    };

    if (pal instanceof Window) {
      pal.center();
      pal.show();
    } else {
      pal.layout.layout(true);
    }

    return pal;
  }

  buildUI(thisObj);
})(this);

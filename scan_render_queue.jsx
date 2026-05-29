/**
 * AE Render Manager — read render queue and write scan_result.json.
 * Requires scan_args.json in %LOCALAPDATA%/AERenderManager/ (written by the app).
 * Manual test: open your .aep in AE, then run this file from File → Scripts → Run Script File.
 */
(function () {
  var outPath = "";
  var projectPath = "";
  var autoQuit = false;

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
    try {
      if (Folder.userData && Folder.userData.fsName) {
        return Folder.userData.fsName;
      }
    } catch (e2) {}
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

  function debugLog(msg) {
    try {
      var f = new File(workDir() + "/scan_debug.log");
      f.encoding = "UTF-8";
      f.open("a");
      f.writeln(new Date().toUTCString() + " " + msg);
      f.close();
    } catch (e) {}
  }

  function safeStringify(obj) {
    if (typeof JSON !== "undefined" && JSON.stringify) {
      try {
        return JSON.stringify(obj);
      } catch (e1) {
        try {
          return JSON.stringify(obj, null, 0);
        } catch (e2) {}
      }
    }
    return obj.toSource();
  }

  function readArgsFile() {
    var argsPath = workDir() + "/scan_args.json";
    var f = new File(argsPath);
    if (f.exists) {
      f.open("r");
      f.encoding = "UTF-8";
      var text = f.read();
      f.close();
      var args;
      if (typeof JSON !== "undefined" && JSON.parse) {
        args = JSON.parse(text);
      } else {
        args = eval("(" + text + ")");
      }
      return args;
    }
    var openProject = getOpenProjectPath();
    if (openProject) {
      debugLog("scan_args.json missing — using open project: " + openProject);
      return {
        project: openProject,
        output: workDir() + "/scan_result.json",
        auto_quit: false,
      };
    }
    throw new Error(
      "scan_args.json not found in " +
        workDir() +
        ". Click Scan in AE Render Manager first (creates the file), or open your .aep in AE before running this script."
    );
  }

  function writeJson(path, obj) {
    var f = new File(path);
    f.encoding = "UTF-8";
    if (!f.parent.exists) {
      f.parent.create();
    }
    f.open("w");
    f.write(safeStringify(obj));
    f.close();
  }

  function writeError(err) {
    var msg = String(err);
    debugLog("ERROR: " + msg);
    var payload = {
      error: msg,
      project: projectPath || "",
      items: [],
    };
    if (outPath) {
      try {
        writeJson(outPath, payload);
      } catch (e2) {
        debugLog("writeJson failed: " + e2);
      }
    }
    var ef = new File(workDir() + "/scan_last_error.txt");
    ef.encoding = "UTF-8";
    ef.open("w");
    ef.write(msg);
    ef.close();
  }

  function finishOk(itemCount) {
    var msg =
      "AE Render Manager scan OK: " +
      itemCount +
      " render queue item(s).\nResult: " +
      outPath;
    debugLog(msg);
    if (autoQuit) {
      try {
        app.quit();
      } catch (eQuit) {}
    } else {
      try {
        alert(msg);
      } catch (eAlert) {}
    }
  }

  function finishErr(err) {
    writeError(err);
    var msg = "AE Render Manager scan failed:\n" + String(err);
    if (autoQuit) {
      try {
        app.quit();
      } catch (eQuit) {}
    } else {
      try {
        alert(msg);
      } catch (eAlert) {}
    }
  }

  function normPath(p) {
    return String(p).replace(/\\/g, "/");
  }

  function pathsEqual(a, b) {
    return normPath(a).toLowerCase() === normPath(b).toLowerCase();
  }

  function statusLabel(status) {
    try {
      if (status === RQItemStatus.QUEUED) return "QUEUED";
      if (status === RQItemStatus.NEEDS_OUTPUT) return "NEEDS_OUTPUT";
      if (status === RQItemStatus.UNQUEUED) return "UNQUEUED";
      if (status === RQItemStatus.RENDERING) return "RENDERING";
      if (status === RQItemStatus.DONE) return "DONE";
      if (status === RQItemStatus.USER_STOPPED) return "USER_STOPPED";
      if (status === RQItemStatus.ERR_STOPPED) return "ERR_STOPPED";
    } catch (e) {}
    return "UNKNOWN(" + status + ")";
  }

  function isQueueable(status) {
    try {
      return (
        status === RQItemStatus.QUEUED ||
        status === RQItemStatus.NEEDS_OUTPUT ||
        status === RQItemStatus.UNQUEUED
      );
    } catch (e) {
      return true;
    }
  }

  /** True when RQ item uses proxies (AE key is "Proxy Use", not "Use Proxies"). */
  function parseProxyUseString(val) {
    if (val === undefined || val === null) {
      return null;
    }
    if (typeof val === "boolean") {
      return val;
    }
    var s = String(val).toLowerCase();
    if (!s) {
      return null;
    }
    if (s.indexOf("use all") >= 0 || s.indexOf("all proxies") >= 0) {
      return true;
    }
    if (s.indexOf("comp prox") >= 0 || s.indexOf("use comp") >= 0) {
      return true;
    }
    if (s.indexOf("no proxy") >= 0 || s.indexOf("use no") >= 0) {
      return false;
    }
    return null;
  }

  /** getSetting("Proxy Use") numeric API: 1 = Use All Proxies (Adobe docs). */
  function parseProxySettingNumber(val) {
    if (typeof val !== "number" || isNaN(val)) {
      return null;
    }
    if (val === 1) {
      return true;
    }
    if (val === 0) {
      return false;
    }
    return null;
  }

  /** renderSettings dropdown index: 1=No, 2=Comp only, 3=All (typical AE menu). */
  function parseProxyMenuIndex(val) {
    if (typeof val !== "number" || isNaN(val)) {
      return parseProxyUseString(val);
    }
    if (val >= 3) {
      return true;
    }
    if (val === 2) {
      return true;
    }
    if (val <= 1) {
      return false;
    }
    return null;
  }

  function readProxyFromRenderSettings(item) {
    var parsed = null;
    try {
      if (item.getSetting) {
        parsed = parseProxyUseString(item.getSetting("Proxy Use-str"));
        if (parsed === null) {
          parsed = parseProxyUseString(item.getSetting("Proxy Use"));
        }
        if (parsed === null) {
          parsed = parseProxySettingNumber(item.getSetting("Proxy Use"));
        }
      }
    } catch (ePxSet) {}
    if (parsed === true) {
      return true;
    }
    if (parsed === false) {
      return false;
    }

    parsed = readProxyFromRenderSettings(item);
    if (parsed === true) {
      return true;
    }
    if (parsed === false) {
      return false;
    }

    try {
      if (comp && comp.useProxy) {
        return true;
      }
    } catch (ePxComp) {}
    try {
      var tpl = String(item.renderSettingsTemplate || "");
      if (/prox/i.test(tpl) && !/no\s*prox/i.test(tpl)) {
        return true;
      }
    } catch (eTpl) {}
    return false;
  }

  function parseSkipExisting(val) {
    if (val === undefined || val === null) {
      return null;
    }
    if (typeof val === "boolean") {
      return val;
    }
    if (typeof val === "number") {
      if (isNaN(val)) {
        return null;
      }
      return val !== 0;
    }
    var s = String(val).toLowerCase();
    if (s === "true" || s === "yes" || s === "1" || s === "on") {
      return true;
    }
    if (s === "false" || s === "no" || s === "0" || s === "off") {
      return false;
    }
    return null;
  }

  function detectSkipExisting(item) {
    var parsed = null;
    try {
      if (item.getSetting) {
        parsed = parseSkipExisting(item.getSetting("Skip Existing Files"));
      }
    } catch (eSkipSet) {}
    if (parsed !== null) {
      return parsed;
    }

    function walk(group, depth) {
      if (!group || depth > 8 || parsed !== null) {
        return;
      }
      var n;
      try {
        n = group.numProperties;
      } catch (e0) {
        return;
      }
      var i;
      for (i = 1; i <= n; i++) {
        var prop;
        try {
          prop = group.property(i);
        } catch (e1) {
          continue;
        }
        if (!prop) {
          continue;
        }
        if (String(prop.name) === "Skip Existing Files") {
          parsed = parseSkipExisting(prop.value);
          if (parsed !== null) {
            return;
          }
        }
        try {
          if (prop.numProperties && prop.numProperties > 0) {
            walk(prop, depth + 1);
          }
        } catch (e2) {}
      }
    }
    try {
      walk(item.renderSettings, 0);
    } catch (eSkipRs) {}
    return parsed === true;
  }

  function timeToFrame(t, comp) {
    if (!comp) return 0;
    try {
      var fd = comp.frameDuration;
      if (!fd || fd <= 0) return 0;
      return Math.round(t / fd);
    } catch (e) {
      return 0;
    }
  }

  function parseFrame(val) {
    if (val === undefined || val === null) return null;
    if (typeof val === "number") {
      if (isNaN(val)) return null;
      return Math.round(val);
    }
    var s = String(val).replace(/^\s+|\s+$/g, "");
    if (!s) return null;
    var n = parseInt(s, 10);
    if (isNaN(n)) return null;
    return n;
  }

  function readSettingFrame(item, key) {
    try {
      if (item.getSetting) {
        return parseFrame(item.getSetting(key));
      }
    } catch (e) {}
    return null;
  }

  function readFramesFromRenderSettings(item) {
    var startF = null;
    var endF = null;
    function walk(group, depth) {
      if (!group || depth > 8) return;
      var n;
      try {
        n = group.numProperties;
      } catch (e0) {
        return;
      }
      var i;
      for (i = 1; i <= n; i++) {
        var prop;
        try {
          prop = group.property(i);
        } catch (e1) {
          continue;
        }
        if (!prop) continue;
        var nm = String(prop.name);
        if (nm === "Start Frame") {
          var sf = parseFrame(prop.value);
          if (sf !== null) startF = sf;
        } else if (nm === "End Frame") {
          var ef = parseFrame(prop.value);
          if (ef !== null) endF = ef;
        }
        try {
          if (prop.numProperties && prop.numProperties > 0) {
            walk(prop, depth + 1);
          }
        } catch (e2) {}
      }
    }
    try {
      walk(item.renderSettings, 0);
    } catch (eRs) {}
    return { start: startF, end: endF };
  }

  function getDisplayStartSeconds(comp) {
    try {
      if (comp && comp.displayStartTime !== undefined && comp.displayStartTime !== null) {
        var dst = comp.displayStartTime;
        if (!isNaN(dst) && dst > 0) return dst;
      }
    } catch (e) {}
    return 0;
  }

  function getWorkAreaFrames(comp) {
    try {
      if (!comp) return { start: null, end: null };
      var waD = comp.workAreaDuration;
      if (waD === undefined || waD <= 0) return { start: null, end: null };
      var start = timeToFrame(comp.workAreaStart, comp);
      var end = timeToFrame(comp.workAreaStart + waD, comp) - 1;
      return { start: start, end: end };
    } catch (e) {}
    return { start: null, end: null };
  }

  /** Frame numbers shown in AE Render Settings (not always equal to timeSpanStart alone). */
  function getFrameRange(item, comp) {
    var startF = readSettingFrame(item, "Start Frame");
    var endF = readSettingFrame(item, "End Frame");

    if (startF === null || endF === null) {
      var rsF = readFramesFromRenderSettings(item);
      if (startF === null) startF = rsF.start;
      if (endF === null) endF = rsF.end;
    }

    var spanStart = 0;
    var spanEnd = 0;
    try {
      spanStart = timeToFrame(item.timeSpanStart, comp);
      spanEnd = timeToFrame(item.timeSpanStart + item.timeSpanDuration, comp) - 1;
    } catch (eTs) {}

    if (startF === null || endF === null) {
      var dst = getDisplayStartSeconds(comp);
      var offStart = timeToFrame(item.timeSpanStart + dst, comp);
      var offEnd =
        timeToFrame(item.timeSpanStart + item.timeSpanDuration + dst, comp) - 1;
      if (startF === null) startF = offStart;
      if (endF === null) endF = offEnd;
    }

    if (endF < startF) endF = startF;

    var wa = getWorkAreaFrames(comp);
    if (
      wa.start !== null &&
      wa.end !== null &&
      spanStart === 0 &&
      wa.start > 0 &&
      spanEnd >= spanStart &&
      (spanEnd - spanStart) === (wa.end - wa.start) &&
      startF <= spanEnd &&
      startF < wa.start
    ) {
      startF = wa.start;
      endF = wa.end;
    }

    return { start: startF, end: endF };
  }

  function ensureProject(path) {
    var projFile = new File(path);
    if (!projFile.exists) {
      throw new Error("Project not found: " + path);
    }
    if (app.project && app.project.file) {
      if (pathsEqual(app.project.file.fsName, path)) {
        debugLog("Project already open: " + path);
        return;
      }
    }
    debugLog("Opening project: " + path);
    app.open(projFile);
    if (!app.project) {
      throw new Error("Could not open project: " + path);
    }
  }

  function getOpenProjectPath() {
    if (!app.project) {
      return "";
    }
    try {
      if (app.project.file && app.project.file.fsName) {
        return app.project.file.fsName;
      }
    } catch (e1) {}
    try {
      if (app.project.fullName) {
        return app.project.fullName;
      }
    } catch (e2) {}
    return "";
  }

  function runScan() {
    debugLog("runScan start");
    debugLog("workDir=" + workDir());
    var args = readArgsFile();
    projectPath = args.project;
    outPath = args.output;
    autoQuit = args.auto_quit === true || args.auto_quit === "true";

    if (!projectPath || !outPath) {
      throw new Error("scan_args.json requires project and output");
    }

    projectPath = new File(projectPath).fsName;
    outPath = new File(outPath).fsName;
    debugLog("project=" + projectPath);
    debugLog("output=" + outPath);

    ensureProject(projectPath);

    var items = [];
    var rq = app.project.renderQueue;
    var n = rq.numItems;
    debugLog("renderQueue items: " + n);

    for (var i = 1; i <= n; i++) {
      var item = rq.item(i);
      if (!item) continue;
      var comp = item.comp;
      if (!comp) continue;

      var output = "";
      var omTemplate = "";
      try {
        var om = item.outputModule(1);
        if (om && om.file) {
          output = om.file.fsName;
        }
        if (om && om.template) {
          omTemplate = String(om.template);
        }
      } catch (eOm) {}

      var frameRange = getFrameRange(item, comp);
      var startF = frameRange.start;
      var endF = frameRange.end;

      var rsTemplate = "";
      var skipExisting = detectSkipExisting(item);
      var useProxy = detectUseProxy(item, comp);
      try {
        rsTemplate = String(item.renderSettingsTemplate || "");
      } catch (eRs) {}

      var st = item.status;
      items.push({
        rq_index: i,
        comp: comp.name,
        status: statusLabel(st),
        start: startF,
        end: endF,
        increment: 1,
        output: output,
        rs_template: rsTemplate,
        om_template: omTemplate,
        skip_existing: skipExisting,
        use_proxy: useProxy,
        queueable: isQueueable(st),
        render_enabled: item.render,
      });
    }

    writeJson(outPath, {
      project: app.project.file ? app.project.file.fsName : projectPath,
      items: items,
    });
    debugLog("wrote " + items.length + " items");
    finishOk(items.length);
  }

  try {
    runScan();
  } catch (err) {
    finishErr(err);
  }
})();

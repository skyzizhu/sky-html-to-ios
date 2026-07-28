"use strict";

function counts(values) {
  const result = new Map();
  for (const value of values || []) result.set(value, (result.get(value) || 0) + 1);
  return result;
}

function overlap(leftValues, rightValues) {
  const left = counts(leftValues);
  const right = counts(rightValues);
  let intersection = 0;
  let leftTotal = 0;
  let rightTotal = 0;
  for (const value of left.values()) leftTotal += value;
  for (const value of right.values()) rightTotal += value;
  for (const [key, value] of left) intersection += Math.min(value, right.get(key) || 0);
  const union = leftTotal + rightTotal - intersection;
  return {
    containment: leftTotal ? intersection / leftTotal : 0,
    reverseContainment: rightTotal ? intersection / rightTotal : 0,
    jaccard: union ? intersection / union : 0,
    intersection,
  };
}

function explicitStateKind(screen) {
  const value = String(screen.iosStateKind || screen.presentationStyle || "").toLowerCase();
  if (/swipe|cell-action/.test(value)) return { kind: "local-effect", localEffect: "swipe-actions" };
  if (/revealed-content|expand|collapse|accordion/.test(value)) {
    return { kind: "local-effect", localEffect: "revealed-content" };
  }
  if (/sheet/.test(value)) return { kind: "presentation", presentationStyle: "sheet" };
  if (/popover/.test(value)) return { kind: "presentation", presentationStyle: "popover" };
  if (/alert|dialog/.test(value)) return { kind: "presentation", presentationStyle: "alert" };
  if (/menu|drawer/.test(value)) return { kind: "presentation", presentationStyle: "menu" };
  if (/overlay|modal|fullscreen|full-screen/.test(value)) return { kind: "presentation", presentationStyle: "overlay" };
  return value ? { kind: "local-effect", localEffect: value } : null;
}

function inferredStateKind(variant) {
  const snapshot = variant.visualSnapshot || {};
  const hint = `${variant.id} ${variant.title || ""} ${(snapshot.hints || []).join(" ")}`.toLowerCase();
  if (/swipe|reveal|delete-action|cell-action|trailing-action/.test(hint)) {
    return { kind: "local-effect", localEffect: "swipe-actions" };
  }
  if (/sheet|bottom-sheet/.test(hint)) return { kind: "presentation", presentationStyle: "sheet" };
  if (/popover/.test(hint)) return { kind: "presentation", presentationStyle: "popover" };
  if (/alert|dialog/.test(hint)) return { kind: "presentation", presentationStyle: "alert" };
  if (/menu|drawer|dropdown/.test(hint)) return { kind: "presentation", presentationStyle: "menu" };
  if (/overlay|modal|mask|scrim/.test(hint) || Number(snapshot.positionedAreaRatio || 0) >= 0.2) {
    return { kind: "presentation", presentationStyle: "overlay" };
  }
  if (Number(snapshot.deltaAreaRatio || 0) <= 0.22) {
    return { kind: "local-effect", localEffect: "revealed-content" };
  }
  return { kind: "local-effect", localEffect: "visual-state" };
}

function structuralMatch(owner, variant) {
  const ownerSnapshot = owner.visualSnapshot || {};
  const variantSnapshot = variant.visualSnapshot || {};
  const structure = overlap(ownerSnapshot.structureTokens, variantSnapshot.structureTokens);
  const text = overlap(ownerSnapshot.textTokens, variantSnapshot.textTokens);
  const ownerCount = Number(ownerSnapshot.nodeCount || 0);
  const variantCount = Number(variantSnapshot.nodeCount || 0);
  const addedRatio = ownerCount ? Math.max(variantCount - ownerCount, 0) / ownerCount : 1;
  const confidence = Math.min(
    0.99,
    structure.containment * 0.55 + structure.jaccard * 0.25 + text.containment * 0.2,
  );
  return {
    qualifies: (
      ownerCount > 0
      && variantCount >= ownerCount * 0.9
      && addedRatio <= 0.55
      && structure.containment >= 0.9
      && structure.jaccard >= 0.66
      && (text.containment >= 0.6 || (ownerSnapshot.textTokens || []).length <= 2)
    ),
    confidence,
    structureContainment: structure.containment,
    structureJaccard: structure.jaccard,
    textContainment: text.containment,
    addedRatio,
  };
}

function classifyScreenRepresentations(screens) {
  const visualStates = [];
  const warnings = [];
  const nativeScreens = screens.filter((screen) =>
    screen.kind === "virtual-screen-state"
    && screen.includeInNativeConversion !== false
  );
  const byHint = new Map();
  for (const screen of nativeScreens) {
    byHint.set(screen.id, screen);
    if (screen.virtualStateId) byHint.set(screen.virtualStateId, screen);
    if (screen.sourceElementId) byHint.set(screen.sourceElementId, screen);
  }

  for (let index = 0; index < nativeScreens.length; index += 1) {
    const variant = nativeScreens[index];
    let owner = variant.iosStateOwner ? byHint.get(variant.iosStateOwner) : null;
    let evidence = null;
    if (variant.iosStateOwner && !owner) {
      warnings.push(
        `State representation ${variant.id} declares unknown owner ${variant.iosStateOwner}; kept as a native screen.`,
      );
    }
    if (!owner) {
      const candidates = nativeScreens
        .slice(0, index)
        .filter((candidate) => !candidate.nativeOwnerScreenId);
      const ranked = candidates
        .map((candidate) => ({ candidate, match: structuralMatch(candidate, variant) }))
        .filter((item) => item.match.qualifies)
        .sort((left, right) => right.match.confidence - left.match.confidence);
      if (ranked.length) {
        owner = ranked[0].candidate;
        evidence = ranked[0].match;
        if (
          ranked.length > 1
          && ranked[0].match.confidence - ranked[1].match.confidence < 0.035
        ) {
          warnings.push(
            `State representation ${variant.id} is similarly close to ${ranked[0].candidate.id} and ${ranked[1].candidate.id}; selected ${ranked[0].candidate.id}. Add data-ios-state-owner to make ownership deterministic.`,
          );
        }
      }
    }
    if (!owner || owner.id === variant.id) continue;

    const explicitKind = explicitStateKind(variant);
    const stateKind = explicitKind || inferredStateKind(variant);
    const stateID = `${owner.id}.${stateKind.localEffect || stateKind.presentationStyle || "state"}.${visualStates.length + 1}`;
    const confidence = variant.iosStateOwner ? 1 : evidence.confidence;
    const state = {
      id: stateID,
      ownerScreenId: owner.id,
      representationScreenId: variant.id,
      kind: stateKind.kind,
      presentationStyle: stateKind.presentationStyle || null,
      localEffect: stateKind.localEffect || null,
      sourceSelector: variant.rootSelector || null,
      activation: variant.activation || null,
      confidence,
      explicit: Boolean(variant.iosStateOwner || explicitKind),
      evidence: variant.iosStateOwner
        ? [`data-ios-state-owner:${variant.iosStateOwner}`]
        : [
            `structure-containment:${evidence.structureContainment.toFixed(3)}`,
            `structure-jaccard:${evidence.structureJaccard.toFixed(3)}`,
            `text-containment:${evidence.textContainment.toFixed(3)}`,
            `added-ratio:${evidence.addedRatio.toFixed(3)}`,
          ],
    };
    variant.kind = "visual-state-representation";
    variant.includeInNativeConversion = false;
    variant.nativeOwnerScreenId = owner.id;
    variant.stateRepresentation = state;
    owner.stateRepresentations = [...(owner.stateRepresentations || []), state];
    visualStates.push(state);
    warnings.push(
      `Deduplicated ${variant.id} into ${owner.id} as ${state.kind}:${state.presentationStyle || state.localEffect}.`,
    );
  }
  return { screens, visualStates, warnings };
}

module.exports = { classifyScreenRepresentations, overlap, structuralMatch };

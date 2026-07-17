#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build"
DERIVED_DATA="$BUILD_DIR/DerivedData"
PRODUCTS_DIR="$DERIVED_DATA/Build/Products/Release-iphoneos"
IPA_DIR="$ROOT_DIR/dist"
IPA_PATH="$IPA_DIR/OPLINKStreamTest-unsigned.ipa"

cd "$ROOT_DIR"
rm -rf "$BUILD_DIR/Payload" "$IPA_PATH"
mkdir -p "$IPA_DIR"

xcodebuild \
  -project OPLINKStreamTest.xcodeproj \
  -scheme OPLINKStreamTest \
  -configuration Release \
  -sdk iphoneos \
  -destination "generic/platform=iOS" \
  -derivedDataPath "$DERIVED_DATA" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  build

APP_PATH="$PRODUCTS_DIR/OPLINKStreamTest.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Cannot find built app at $APP_PATH" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR/Payload"
cp -R "$APP_PATH" "$BUILD_DIR/Payload/"
(
  cd "$BUILD_DIR"
  zip -qry "$IPA_PATH" Payload
)

echo "$IPA_PATH"


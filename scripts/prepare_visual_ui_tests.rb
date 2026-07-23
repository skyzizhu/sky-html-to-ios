#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "json"
require "optparse"

begin
  require "xcodeproj"
rescue LoadError
  abort "The xcodeproj Ruby gem is required. Install it with: gem install xcodeproj"
end

options = { minimum_ios: nil }
OptionParser.new do |parser|
  parser.on("--project PATH") { |value| options[:project] = value }
  parser.on("--target NAME") { |value| options[:target] = value }
  parser.on("--manifest PATH") { |value| options[:manifest] = value }
  parser.on("--source-dir PATH") { |value| options[:source_dir] = value }
  parser.on("--minimum-ios VERSION") { |value| options[:minimum_ios] = value }
end.parse!

abort "--project, --target, --manifest and --source-dir are required" unless %i[project target manifest source_dir].all? { |key| options[key] }

project_path = File.expand_path(options[:project])
manifest_path = File.expand_path(options[:manifest])
source_dir = File.expand_path(options[:source_dir])
project = Xcodeproj::Project.open(project_path)
app_target = project.targets.find { |target| target.name == options[:target] }
abort "Application target not found: #{options[:target]}" unless app_target
abort "Target is not an application: #{options[:target]}" unless app_target.product_type == Xcodeproj::Constants::PRODUCT_TYPE_UTI[:application]

manifest = JSON.parse(File.read(manifest_path))
abort "Unsupported visual state manifest" unless manifest["schemaVersion"] == "visual-state-manifest-1.0"

def swift_string(value)
  value.to_s.dump
end

def swift_identifier(value, index)
  usable = value.to_s.gsub(/[^A-Za-z0-9_]/, "_")
  usable = "state_#{usable}" unless usable.match?(/^[A-Za-z_]/)
  "test_#{index}_#{usable}"
end

def action_source(action)
  identifier = swift_string(action["accessibilityIdentifier"])
  case action["type"]
  when "tap"
    lines = ["try tapElement(identifier: #{identifier}, in: app)"]
    assertion = action["assertion"] || {}
    if assertion["type"] == "not-exists"
      target = swift_string(assertion["accessibilityIdentifier"])
      lines << "try assertElementAbsent(identifier: #{target}, in: app)"
    end
    lines.join("\n        ")
  when "scroll"
    position = swift_string(action["position"] || "top")
    "try scrollElement(identifier: #{identifier}, position: #{position}, in: app)"
  when "fill"
    value = swift_string(action["value"] || "")
    "try fillElement(identifier: #{identifier}, value: #{value}, in: app)"
  when "check"
    "try setSwitch(identifier: #{identifier}, enabled: true, in: app)"
  when "uncheck"
    "try setSwitch(identifier: #{identifier}, enabled: false, in: app)"
  else
    abort "Unsupported iOS visual action: #{action["type"]}"
  end
end

state_methods = (manifest["states"] || []).each_with_index.map do |state, index|
  actions = (state["iosActions"] || []).map { |action| "        #{action_source(action)}" }.join("\n")
  actions = "        _ = app" if actions.empty?
  <<~SWIFT
      func #{swift_identifier(state["id"], index)}() throws {
          let app = launchApp()
  #{actions}
          capture(name: #{swift_string(state["id"] + ".png")})
      }
  SWIFT
end.join("\n")

locale = manifest["locale"].to_s.strip
arguments = []
unless locale.empty?
  apple_locale = locale.tr("-", "_")
  arguments += ["-AppleLanguages", "(#{locale})", "-AppleLocale", apple_locale]
end
screen_id = manifest["screenId"].to_s.strip
arguments += ["-HTMLToIOSInitialRoute", screen_id] unless screen_id.empty?
launch_arguments = "[#{arguments.map { |item| swift_string(item) }.join(", ")}]"

swift = <<~SWIFT
  import XCTest

  final class HTMLToIOSVisualStateTests: XCTestCase {
      override func setUpWithError() throws {
          continueAfterFailure = false
      }

      private func launchApp() -> XCUIApplication {
          let app = XCUIApplication()
          app.launchArguments += #{launch_arguments}
          app.launchArguments += ["-UIAnimationsEnabled", "NO"]
          app.launch()
          return app
      }

      private func element(identifier: String, in app: XCUIApplication) throws -> XCUIElement {
          let candidate = app.descendants(matching: .any).matching(identifier: identifier).firstMatch
          guard candidate.waitForExistence(timeout: 5) else {
              throw XCTSkip("Missing accessibility identifier: \\(identifier)")
          }
          return candidate
      }

      private func tapElement(identifier: String, in app: XCUIApplication) throws {
          let candidate = try element(identifier: identifier, in: app)
          candidate.tap()
      }

      private func assertElementAbsent(identifier: String, in app: XCUIApplication) throws {
          let candidate = app.descendants(matching: .any).matching(identifier: identifier).firstMatch
          XCTAssertTrue(candidate.waitForNonExistence(timeout: 3), "Expected element to disappear: \(identifier)")
      }

      private func fillElement(identifier: String, value: String, in app: XCUIApplication) throws {
          let candidate = try element(identifier: identifier, in: app)
          candidate.tap()
          if let current = candidate.value as? String, !current.isEmpty {
              candidate.press(forDuration: 0.8)
              app.menuItems["Select All"].tap()
          }
          candidate.typeText(value)
      }

      private func setSwitch(identifier: String, enabled: Bool, in app: XCUIApplication) throws {
          let candidate = try element(identifier: identifier, in: app)
          let current = (candidate.value as? String) == "1"
          if current != enabled { candidate.tap() }
      }

      private func scrollElement(identifier: String, position: String, in app: XCUIApplication) throws {
          let candidate = try element(identifier: identifier, in: app)
          guard position != "top" else { return }
          let repetitions = position == "bottom" ? 6 : 2
          for _ in 0..<repetitions { candidate.swipeUp(velocity: .fast) }
      }

      private func capture(name: String) {
          RunLoop.current.run(until: Date().addingTimeInterval(0.35))
          let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
          attachment.name = name
          attachment.lifetime = .keepAlways
          add(attachment)
      }

  #{state_methods}
  }
SWIFT

FileUtils.mkdir_p(source_dir)
source_file = File.join(source_dir, "HTMLToIOSVisualStateTests.swift")
File.write(source_file, swift)

test_target_name = "HTMLToIOSVisualTests"
test_target = project.targets.find { |target| target.name == test_target_name }
minimum_ios = options[:minimum_ios] || app_target.build_configurations.filter_map { |configuration| configuration.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] }.first || "16.0"
managed_marker = test_target&.build_configurations&.any? { |configuration| configuration.build_settings["HTML_TO_IOS_MANAGED_VISUAL_TESTS"] == "YES" }
legacy_managed_source = test_target&.source_build_phase&.files_references&.any? { |reference| reference.real_path.to_s.include?("/.html-to-ios/") }
if test_target && !managed_marker && !legacy_managed_source
  abort "Refusing to modify existing non-managed target: #{test_target_name}"
end
unless test_target
  test_target = project.new_target(:ui_test_bundle, test_target_name, :ios, minimum_ios)
  test_target.add_dependency(app_target)
  test_target.add_system_framework("XCTest")
end

group = project.main_group.find_subpath("HTMLToIOSVisualTests", true)
group.set_source_tree("<group>")
group.set_path(source_dir)
group.files.each { |reference| reference.remove_from_project unless File.exist?(reference.real_path) }
reference = group.files.find { |item| item.path == File.basename(source_file) } || group.new_file(source_file)
unless test_target.source_build_phase.files_references.include?(reference)
  test_target.source_build_phase.add_file_reference(reference)
end

app_bundle_id = app_target.build_configurations.filter_map { |configuration| configuration.build_settings["PRODUCT_BUNDLE_IDENTIFIER"] }.first || "com.example.app"
test_target.build_configurations.each do |configuration|
  settings = configuration.build_settings
  settings["PRODUCT_BUNDLE_IDENTIFIER"] = "#{app_bundle_id}.htmltoiosvisualtests"
  settings["IPHONEOS_DEPLOYMENT_TARGET"] = minimum_ios
  settings["SWIFT_VERSION"] = "5.0"
  settings["GENERATE_INFOPLIST_FILE"] = "YES"
  settings["CODE_SIGN_STYLE"] = "Automatic"
  settings["TEST_TARGET_NAME"] = app_target.name
  settings["TARGETED_DEVICE_FAMILY"] = "1,2"
  settings["HTML_TO_IOS_MANAGED_VISUAL_TESTS"] = "YES"
end

project.save
scheme_name = "HTMLToIOSVisualValidation"
scheme = Xcodeproj::XCScheme.new
scheme.configure_with_targets(app_target, test_target)
scheme.save_as(project_path, scheme_name, true)

puts JSON.pretty_generate(
  schemaVersion: "html-to-ios-visual-test-target-1.0",
  project: project_path,
  appTarget: app_target.name,
  testTarget: test_target.name,
  scheme: scheme_name,
  source: source_file,
  states: (manifest["states"] || []).length
)

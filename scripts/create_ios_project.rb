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

options = {
  ui_stack: "swiftui",
  minimum_ios: "16.0",
  bundle_id: nil
}

OptionParser.new do |parser|
  parser.on("--root PATH") { |value| options[:root] = value }
  parser.on("--name NAME") { |value| options[:name] = value }
  parser.on("--ui-stack STACK", %w[swiftui uikit]) { |value| options[:ui_stack] = value }
  parser.on("--minimum-ios VERSION") { |value| options[:minimum_ios] = value }
  parser.on("--bundle-id IDENTIFIER") { |value| options[:bundle_id] = value }
end.parse!

abort "--root and --name are required" unless options[:root] && options[:name]

root = File.expand_path(options[:root])
name = options[:name].gsub(/[^A-Za-z0-9_-]/, "")
abort "Project name has no usable characters" if name.empty?
identifier = name.gsub(/[^A-Za-z0-9_]/, "")
identifier = "App#{identifier}" if identifier.match?(/^\d/)
bundle_id = options[:bundle_id] || "com.example.#{name.downcase.gsub(/[^a-z0-9]/, "")}"
project_path = File.join(root, "#{name}.xcodeproj")
source_dir = File.join(root, name)

existing = Dir.glob(File.join(root, "**", "*.{xcodeproj,xcworkspace}"))
abort "An Xcode project/workspace already exists under #{root}: #{existing.first}" unless existing.empty?
abort "Refusing to overwrite existing project path: #{project_path}" if File.exist?(project_path)

FileUtils.mkdir_p(source_dir)

swiftui_app = <<~SWIFT
  import SwiftUI

  @main
  struct #{identifier}App: App {
      var body: some Scene {
          WindowGroup {
              ContentView()
          }
      }
  }
SWIFT

swiftui_content = <<~SWIFT
  import SwiftUI

  struct ContentView: View {
      var body: some View {
          Text("#{name}")
              .padding()
      }
  }

  #Preview {
      ContentView()
  }
SWIFT

uikit_delegate = <<~SWIFT
  import UIKit

  @main
  final class AppDelegate: UIResponder, UIApplicationDelegate {
      var window: UIWindow?

      func application(
          _ application: UIApplication,
          didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
      ) -> Bool {
          let window = UIWindow(frame: UIScreen.main.bounds)
          window.rootViewController = ViewController()
          window.makeKeyAndVisible()
          self.window = window
          return true
      }
  }
SWIFT

uikit_controller = <<~SWIFT
  import UIKit

  final class ViewController: UIViewController {
      override func viewDidLoad() {
          super.viewDidLoad()
          view.backgroundColor = .systemBackground

          let label = UILabel()
          label.translatesAutoresizingMaskIntoConstraints = false
          label.text = "#{name}"
          label.font = .preferredFont(forTextStyle: .title1)
          view.addSubview(label)

          NSLayoutConstraint.activate([
              label.centerXAnchor.constraint(equalTo: view.centerXAnchor),
              label.centerYAnchor.constraint(equalTo: view.centerYAnchor)
          ])
      }
  }
SWIFT

files = if options[:ui_stack] == "swiftui"
          { "#{identifier}App.swift" => swiftui_app, "ContentView.swift" => swiftui_content }
        else
          { "AppDelegate.swift" => uikit_delegate, "ViewController.swift" => uikit_controller }
        end
files.each { |filename, content| File.write(File.join(source_dir, filename), content) }

assets_dir = File.join(source_dir, "Assets.xcassets")
FileUtils.mkdir_p(assets_dir)
File.write(File.join(assets_dir, "Contents.json"), JSON.pretty_generate({ "info" => { "author" => "xcode", "version" => 1 } }) + "\n")

project = Xcodeproj::Project.new(project_path)
target = project.new_target(:application, name, :ios, options[:minimum_ios])
group = project.main_group.new_group(name, name)
files.each_key do |filename|
  reference = group.new_file(filename)
  target.source_build_phase.add_file_reference(reference)
end
assets_reference = group.new_file("Assets.xcassets")
target.resources_build_phase.add_file_reference(assets_reference)

target.build_configurations.each do |configuration|
  settings = configuration.build_settings
  settings["PRODUCT_BUNDLE_IDENTIFIER"] = bundle_id
  settings["IPHONEOS_DEPLOYMENT_TARGET"] = options[:minimum_ios]
  settings["SWIFT_VERSION"] = "5.0"
  settings["GENERATE_INFOPLIST_FILE"] = "YES"
  settings["INFOPLIST_KEY_UILaunchScreen_Generation"] = "YES"
  settings["TARGETED_DEVICE_FAMILY"] = "1,2"
  settings["CODE_SIGN_STYLE"] = "Automatic"
  settings["ASSETCATALOG_COMPILER_APPICON_NAME"] = ""
  settings["ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME"] = ""
end

project.save
scheme = Xcodeproj::XCScheme.new
scheme.configure_with_targets(target, nil)
scheme.save_as(project_path, name, true)

puts JSON.pretty_generate(
  schemaVersion: "created-ios-project-1.0",
  project: project_path,
  sourceDirectory: source_dir,
  name: name,
  uiStack: options[:ui_stack],
  minimumIOS: options[:minimum_ios],
  bundleIdentifier: bundle_id,
  scheme: name
)

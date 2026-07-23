#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "optparse"
require "pathname"

begin
  require "xcodeproj"
rescue LoadError
  abort "The xcodeproj Ruby gem is required. Install it with: gem install xcodeproj"
end

options = {}
OptionParser.new do |parser|
  parser.on("--project PATH") { |value| options[:project] = value }
  parser.on("--target NAME") { |value| options[:target] = value }
  parser.on("--generated-dir PATH") { |value| options[:generated_dir] = value }
  parser.on("--minimum-ios VERSION") { |value| options[:minimum_ios] = value }
end.parse!

abort "--project, --target, and --generated-dir are required" unless options.values_at(:project, :target, :generated_dir).all?

project_path = File.expand_path(options[:project])
generated_dir = File.expand_path(options[:generated_dir])
abort "Project does not exist: #{project_path}" unless File.directory?(project_path)
abort "Generated directory does not exist: #{generated_dir}" unless File.directory?(generated_dir)

project = Xcodeproj::Project.open(project_path)
target = project.targets.find { |candidate| candidate.name == options[:target] }
abort "Target not found: #{options[:target]}" unless target

generated_path = Pathname.new(generated_dir)
candidate_groups = project.groups.filter_map do |group|
  begin
    real_path = Pathname.new(group.real_path.to_s).expand_path
    relative = generated_path.relative_path_from(real_path)
    next if relative.to_s.start_with?("..")

    [group, relative.each_filename.to_a]
  rescue ArgumentError
    nil
  end
end

base_group, components = candidate_groups.min_by { |(_group, parts)| parts.length } || [project.main_group, []]
group = components.reduce(base_group) do |parent, component|
  parent.groups.find { |child| child.path == component || child.display_name == component } || parent.new_group(component, component)
end

removed_stale_references = []
project.files.dup.each do |reference|
  absolute = File.expand_path(reference.real_path.to_s)
  next unless absolute == generated_dir || absolute.start_with?(generated_dir + File::SEPARATOR)
  next if File.exist?(absolute)

  target.build_phases.each do |phase|
    next unless phase.respond_to?(:files_references) && phase.respond_to?(:remove_file_reference)
    phase.remove_file_reference(reference) if phase.files_references.include?(reference)
  end
  removed_stale_references << absolute
  reference.remove_from_project
end

source_files = Dir.glob(File.join(generated_dir, "**", "*.swift")).sort
abort "No generated Swift files found under #{generated_dir}" if source_files.empty?
resource_files = Dir.glob(File.join(generated_dir, "**", "*.json")).reject do |path|
  File.basename(path).start_with?(".") || path.split(File::SEPARATOR).any? { |part| part.end_with?(".xcassets") }
end
resource_files.concat(Dir.glob(File.join(generated_dir, "**", "*.xcassets")))
resource_files.concat(Dir.glob(File.join(generated_dir, "**", "*.{ttf,otf}")))
resource_files = resource_files.uniq.sort

existing_references = project.files.each_with_object({}) do |reference, memo|
  memo[File.expand_path(reference.real_path.to_s)] = reference
end
added = []
already_integrated = []
resources_added = []
resources_already_integrated = []

source_files.each do |source_file|
  absolute = File.expand_path(source_file)
  reference = existing_references[absolute]
  unless reference
    relative = Pathname.new(absolute).relative_path_from(Pathname.new(generated_dir)).to_s
    reference = group.new_file(relative)
    existing_references[absolute] = reference
  end

  if target.source_build_phase.files_references.include?(reference)
    already_integrated << absolute
  else
    target.source_build_phase.add_file_reference(reference)
    added << absolute
  end
end

resource_files.each do |resource_file|
  absolute = File.expand_path(resource_file)
  reference = existing_references[absolute]
  unless reference
    relative = Pathname.new(absolute).relative_path_from(Pathname.new(generated_dir)).to_s
    reference = group.new_file(relative)
    existing_references[absolute] = reference
  end

  if target.resources_build_phase.files_references.include?(reference)
    resources_already_integrated << absolute
  else
    target.resources_build_phase.add_file_reference(reference)
    resources_added << absolute
  end
end

if options[:minimum_ios]
  target.build_configurations.each do |configuration|
    configuration.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] = options[:minimum_ios]
  end
end

project.save
puts JSON.pretty_generate(
  schemaVersion: "integrated-generated-sources-1.0",
  project: project_path,
  target: target.name,
  generatedDirectory: generated_dir,
  minimumIOS: options[:minimum_ios],
  added: added,
  alreadyIntegrated: already_integrated,
  resourcesAdded: resources_added,
  resourcesAlreadyIntegrated: resources_already_integrated,
  removedStaleReferences: removed_stale_references
)

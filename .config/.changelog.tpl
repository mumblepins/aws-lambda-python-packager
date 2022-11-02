{{#general_title}}
# {{{title}}}


{{/general_title}}
{{#versions}}
## {{{label}}}

{{#sections}}
### {{{label}}}

{{#commits}}
  * [{{commit.date}}]({{commit.sha1}}) – {{{subject}}}  <small>([{{{author}}}](mailto:{{{commit.author_email}}}))</small>
{{#body}}

{{{body_indented}}}
{{/body}}

{{/commits}}
{{/sections}}

{{/versions}}

#!/usr/bin/env bash

TO_REPLACE_UNDER=python_template_repo
TO_REPLACE_DASH=python-template-repo
read -rp 'Enter new project name: ' project_name_dash
project_name_under=$(echo "$project_name_dash" | tr '-' '_')

echo "Renaming to $project_name_dash"
grep -lr --exclude='project_init.sh' --exclude-dir='.git' "$TO_REPLACE_DASH" . \
    | tee >(cat 1>&2) \
    | xargs -I{} sed -i 's|'"$TO_REPLACE_DASH"'|'"$project_name_dash"'|g' {}
find . -iname '*'"$TO_REPLACE_DASH"'*' \
    | tee >(cat 1>&2) \
    | sed -E 's|(.*/)+(.+)\.(.+)$|\1\2.\3 \1'"$project_name_dash"'.\3|' \
    | xargs -r -n2 mv $1 $2
grep -lr --exclude='project_init.sh' --exclude-dir='.git' "$TO_REPLACE_UNDER" . \
    | tee >(cat 1>&2) \
    | xargs -I{} sed -i 's|'"$TO_REPLACE_UNDER"'|'"$project_name_under"'|g' {}
find . -iname '*'"$TO_REPLACE_UNDER"'*' \
    | tee >(cat 1>&2) \
    | sed -E 's|(.*/)+(.+)\.(.+)$|\1\2.\3 \1'"$project_name_under"'.\3|' \
    | xargs -r -n2 mv $1 $2
#grep -lr --exclude='project_init.sh' 'AUTHOR_NAME_AND_EMAIL' . \
#  | xargs -I{} \
#    sed -i 's|AUTHOR_NAME_AND_EMAIL|'"$(git config user.name) <$(git config user.email)>"'|g' {}

#git init
poetry update
poetry run pre-commit install
poetry run pre-commit autoupdate
